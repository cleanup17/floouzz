"""
Orchestrateur du scan quotidien — collecte + enrichissement via pipeline_ia.

Flux en 2 etapes :
    1. run_collecte()      : lit les sources actives, stocke les decouvertes brutes
    2. run_enrichissement(): pour chaque decouverte non enrichie du jour :
         - deduplication : si un signal proche existe deja (<7j), on skip
         - pipeline_ia   : un seul appel Claude -> format riche (scores 0-10,
                           verdict GO/WATCH/SKIP, resume_fr, YMYL, etc.)
         - log execution : chaque passe de source est tracee dans executions_scanner

Le mode Decouverte et le mode Analyse partagent donc le meme pipeline_ia, avec
le meme cache 24h et les memes contraintes de format. Les sources anglophones
pre-traduisent leurs titres via traduction.traduire_titres() avant stockage.
"""

import logging
import time
from datetime import date, datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Decouverte, ExecutionScanner, Preference, Source, Thematique
from app.services.deduplication import chercher_doublon
from app.services.pipeline_ia import analyser as analyser_pipeline
from app.services.sources.base import fetch_source

logger = logging.getLogger(__name__)

# Limite du nombre de decouvertes enrichies en un scan (maitrise du cout IA)
MAX_DECOUVERTES_PAR_SCAN = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resoudre_source_type(source: Source) -> str:
    """
    Determine le type de fetcher a partir du type declare et de sa config.
    Extrait depuis l'ancien run_collecte pour lisibilite.
    """
    source_type = source.type
    if "fetcher" in source.config:
        return source.config["fetcher"]
    if source.type == "serpapi" and "engine" in source.config:
        engine = source.config["engine"]
        if engine == "google_jobs":
            return "serpapi_jobs"
        if engine == "google":
            return "serpapi_search"
        if engine == "google_news":
            return "serpapi_news"
    return source_type


async def _logger_execution(
    db: AsyncSession,
    source: str,
    statut: str,
    nb_signaux: int,
    duree_ms: int,
    erreur: str | None = None,
) -> None:
    """Ecrit une entree dans executions_scanner pour tracabilite."""
    db.add(ExecutionScanner(
        source=source,
        statut=statut,
        nb_signaux=nb_signaux,
        duree_ms=duree_ms,
        erreur=erreur,
    ))


def _synthese_decouverte(decouverte: Decouverte) -> str:
    """
    Construit une synthese FR injectee dans pipeline_ia.analyser(contenu=...).

    Le contenu pertinent varie selon la source — on prend les champs de donnees
    les plus parlants (titre FR pre-traduit, metriques d'engagement).
    """
    donnees = decouverte.donnees or {}
    titre_fr = donnees.get("titre_fr") or donnees.get("tagline_fr") or decouverte.titre
    source_nom = donnees.get("source", "inconnue")

    # Signaux d'engagement pris selon la source
    engagement = []
    if "commentaires" in donnees:
        engagement.append(f"{donnees['commentaires']} commentaires")
    if "upvotes" in donnees:
        engagement.append(f"{donnees['upvotes']} upvotes")
    if "points" in donnees:
        engagement.append(f"{donnees['points']} points")
    if "votes" in donnees:
        engagement.append(f"{donnees['votes']} votes")
    if "volume" in donnees:
        engagement.append(f"volume {donnees['volume']}")

    eng_str = ", ".join(engagement) if engagement else "signal brut"
    return f"{titre_fr}. Source : {source_nom}. Engagement : {eng_str}."


# ---------------------------------------------------------------------------
# Etape 1 — Collecte
# ---------------------------------------------------------------------------

async def run_collecte() -> int:
    """
    Collecte les signaux depuis toutes les sources actives.
    Chaque source ecoute ce qui monte — pas de mots-cles imposes.
    Chaque passe est loggee dans executions_scanner.
    """
    async with async_session() as db:
        stmt = select(Source).where(Source.actif.is_(True))
        result = await db.execute(stmt)
        sources = result.scalars().all()

        if not sources:
            logger.warning("Aucune source active configuree")
            return 0

        total = 0
        for source in sources:
            debut = time.monotonic()
            nb_source = 0
            statut = "succes"
            erreur_msg: str | None = None

            try:
                source_type = _resoudre_source_type(source)
                results = await fetch_source(source_type, "", source.config)

                for r in results:
                    if r.donnees.get("erreur"):
                        erreur_msg = r.donnees["erreur"]
                        statut = "partiel"
                        logger.warning(f"Erreur source {source.nom}: {erreur_msg}")
                        continue

                    db.add(Decouverte(
                        source_id=source.id,
                        titre=r.titre,
                        url=r.url,
                        donnees=r.donnees,
                        scan_date=date.today(),
                        statut="nouveau",
                    ))
                    nb_source += 1

                total += nb_source

            except Exception as e:
                statut = "echec"
                erreur_msg = str(e)
                logger.error(f"Erreur collecte {source.nom}: {e}")

            duree_ms = int((time.monotonic() - debut) * 1000)
            await _logger_execution(
                db,
                source=source.nom,
                statut=statut,
                nb_signaux=nb_source,
                duree_ms=duree_ms,
                erreur=erreur_msg,
            )

        await db.commit()
        logger.info(f"Collecte terminee : {total} decouvertes stockees")
        return total


# ---------------------------------------------------------------------------
# Etape 2 — Enrichissement via pipeline_ia
# ---------------------------------------------------------------------------

async def run_enrichissement() -> int:
    """
    Enrichit les decouvertes brutes du jour via pipeline_ia.

    Pour chaque decouverte non encore traitee :
      1. pipeline_ia.analyser() -> format riche complet
      2. deduplication : si un signal proche existe dans les 7 derniers jours,
         on marque la decouverte courante comme doublon (statut='ignore')
      3. on persiste les champs pipeline_ia sur la ligne Decouverte

    Chaque batch de source est trace dans executions_scanner.
    """
    async with async_session() as db:
        # Chargement des thematiques actives (alimente le prompt pipeline_ia)
        stmt_themes = select(Thematique.nom).where(Thematique.actif.is_(True))
        result_themes = await db.execute(stmt_themes)
        thematiques = [row[0] for row in result_themes.all()]

        # Chargement des tags marques "ignore" par l'utilisatrice (blacklist).
        # Apres enrichissement, toute decouverte taggee avec l'un d'eux sera
        # passee en statut "ignore" (pas de suppression, juste invisible en UI).
        stmt_prefs = (
            select(Preference)
            .where(Preference.type == "ignore")
            .order_by(Preference.created_at.desc())
            .limit(50)
        )
        result_prefs = await db.execute(stmt_prefs)
        tags_ignores: set[str] = set()
        for p in result_prefs.scalars().all():
            tags_ignores.update(t.lower() for t in (p.tags_associes or []))

        # Decouvertes du jour qui n'ont pas encore ete enrichies
        # (verdict NULL = pas encore passe par pipeline_ia)
        stmt = (
            select(Decouverte)
            .where(Decouverte.scan_date == date.today())
            .where(Decouverte.verdict.is_(None))
            .limit(MAX_DECOUVERTES_PAR_SCAN)
        )
        result = await db.execute(stmt)
        decouvertes = list(result.scalars().all())

        if not decouvertes:
            logger.info("Aucune decouverte a enrichir")
            return 0

        debut = time.monotonic()
        nb_enrichies = 0
        nb_doublons = 0
        nb_ignores_prefs = 0
        erreurs: list[str] = []

        for decouverte in decouvertes:
            try:
                # 1. Enrichissement via pipeline_ia (cache 24h + fallback integres)
                contenu = _synthese_decouverte(decouverte)
                resultat = await analyser_pipeline(
                    titre=decouverte.titre,
                    contenu=contenu,
                    donnees=decouverte.donnees,
                    thematiques=thematiques,
                    session=db,
                    source=(decouverte.donnees or {}).get("source") or "decouverte",
                )

                # 2. Deduplication : cherche un signal proche dans les 7 derniers jours
                # (AVANT d'ecrire les champs, pour pouvoir marquer comme ignore si besoin)
                doublon_id = await chercher_doublon(
                    db,
                    titre=decouverte.titre,
                    tags=resultat["tags"],
                    niche_detectee=resultat["niche_detectee"],
                )

                if doublon_id is not None and doublon_id != decouverte.id:
                    # Doublon detecte : on marque la decouverte courante comme ignoree
                    # mais on enregistre quand meme le resultat pipeline_ia pour audit
                    decouverte.statut = "ignore"
                    nb_doublons += 1

                # 2bis. Filtre preferences "ignore" : si un tag retourne par
                # pipeline_ia est dans la blacklist utilisatrice, on ignore.
                # Comparaison case-insensitive pour coller a la saisie humaine.
                if tags_ignores:
                    tags_resultat = {str(t).lower() for t in resultat["tags"]}
                    if tags_resultat & tags_ignores:
                        decouverte.statut = "ignore"
                        nb_ignores_prefs += 1

                # 3. Ecriture des champs pipeline_ia sur la decouverte
                scores = resultat["scores"]
                decouverte.pipeline_ia = resultat
                decouverte.resume_fr = resultat["resume_fr"]
                decouverte.verdict = resultat["verdict"]
                decouverte.verdict_raison = resultat["verdict_raison"]
                decouverte.score_global = resultat["score_global"]
                decouverte.score_demande = scores["demande"]["valeur"]
                decouverte.score_douleur = scores["douleur"]["valeur"]
                decouverte.score_concurrence = scores["concurrence"]["valeur"]
                decouverte.score_monetisation = scores["monetisation"]["valeur"]
                decouverte.tags = resultat["tags"]
                decouverte.mots_cles_seo = resultat["mots_cles_seo"]
                decouverte.risque_ymyl = resultat["risque_ymyl"]
                decouverte.niche_detectee = resultat["niche_detectee"]

                nb_enrichies += 1

            except Exception as e:
                erreurs.append(f"{decouverte.id}: {e}")
                logger.error(f"Erreur enrichissement decouverte {decouverte.id}: {e}")
                continue

        duree_ms = int((time.monotonic() - debut) * 1000)
        statut = "succes" if not erreurs else ("partiel" if nb_enrichies > 0 else "echec")
        erreur_msg = "; ".join(erreurs[:3]) if erreurs else None

        await _logger_execution(
            db,
            source="pipeline_ia",
            statut=statut,
            nb_signaux=nb_enrichies,
            duree_ms=duree_ms,
            erreur=erreur_msg,
        )

        await db.commit()
        logger.info(
            f"Enrichissement termine : {nb_enrichies} enrichies, "
            f"{nb_doublons} doublons, {nb_ignores_prefs} ignores (prefs), "
            f"{len(erreurs)} erreurs"
        )
        return nb_enrichies


# ---------------------------------------------------------------------------
# Nettoyage du cache IA
# ---------------------------------------------------------------------------

async def nettoyer_cache_ia() -> int:
    """
    Purge les entrees expirees de la table cache_ia.

    Appele automatiquement en fin de run_scan_complet() pour eviter que la
    table grossisse indefiniment. Echec silencieux si la table n'existe pas.
    Retourne le nombre de lignes supprimees.
    """
    async with async_session() as db:
        try:
            result = await db.execute(
                text("DELETE FROM cache_ia WHERE expires_at < NOW()")
            )
            await db.commit()
            nb_supprimees = result.rowcount or 0
            if nb_supprimees:
                logger.info(f"Cache IA : {nb_supprimees} entrees expirees purgees")
            return nb_supprimees
        except Exception as e:
            logger.debug(f"Cache IA : nettoyage ignore ({e})")
            await db.rollback()
            return 0


# ---------------------------------------------------------------------------
# Point d'entree unifie
# ---------------------------------------------------------------------------

async def run_scan_complet() -> dict:
    """Lance collecte + enrichissement + nettoyage cache (bouton Rafraichir)."""
    nb_collectes = await run_collecte()
    nb_enrichies = await run_enrichissement()
    nb_cache_purge = await nettoyer_cache_ia()
    return {
        "nb_collectes": nb_collectes,
        "nb_enrichies": nb_enrichies,
        "nb_cache_purge": nb_cache_purge,
        "date": date.today().isoformat(),
    }
