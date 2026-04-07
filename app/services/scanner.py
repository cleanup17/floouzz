"""Orchestrateur du scan quotidien — collecte + enrichissement."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Decouverte, Preference, Source, Thematique
from app.services.enrichissement import enrichir_decouverte
from app.services.sources.base import fetch_source
from app.services.traduction import traduire_mot_cle

logger = logging.getLogger(__name__)


async def _collect_from_source(
    source_type: str,
    mot_cle: str,
    config: dict,
    source_id: str,
) -> list[dict]:
    """Collecte les signaux d'une source et les formate en dict decouverte."""
    results = await fetch_source(source_type, mot_cle, config)
    decouvertes = []
    for r in results:
        decouvertes.append({
            "source_id": source_id,
            "titre": r.titre,
            "url": r.url,
            "donnees": r.donnees,
            "score_partiel": r.score_partiel,
            "scan_date": date.today(),
        })
    return decouvertes


async def run_collecte(mots_cles: list[str] | None = None) -> int:
    """
    Etape 1 : collecte des signaux depuis toutes les sources actives.
    Si mots_cles est None, utilise des mots-cles generiques pour le scan trending.
    Retourne le nombre de decouvertes stockees.
    """
    async with async_session() as db:
        # Charger les sources actives
        stmt = select(Source).where(Source.actif.is_(True))
        result = await db.execute(stmt)
        sources = result.scalars().all()

        if not sources:
            logger.warning("Aucune source active configuree")
            return 0

        # Mots-cles par defaut pour le scan trending
        if not mots_cles:
            mots_cles = ["AI tools", "SaaS", "automation", "no-code", "freelance"]

        total = 0
        for source in sources:
            for mot_cle in mots_cles:
                try:
                    # Traduire si necessaire
                    mot_cle_en = await traduire_mot_cle(mot_cle)

                    # Determiner le type de fetcher selon la config de la source
                    source_type = source.type
                    # Les sources avec un fetcher specifique dans la config
                    if "fetcher" in source.config:
                        source_type = source.config["fetcher"]
                    elif source.type == "serpapi" and "engine" in source.config:
                        engine = source.config["engine"]
                        if engine == "google_jobs":
                            source_type = "serpapi_jobs"
                        elif engine == "google":
                            source_type = "serpapi_search"
                        elif engine == "google_news":
                            source_type = "serpapi_news"

                    decouvertes_data = await _collect_from_source(
                        source_type=source_type,
                        mot_cle=mot_cle_en,
                        config=source.config,
                        source_id=str(source.id),
                    )

                    for d in decouvertes_data:
                        decouverte = Decouverte(
                            source_id=source.id,
                            titre=d["titre"],
                            url=d.get("url"),
                            donnees=d["donnees"],
                            scan_date=d["scan_date"],
                            statut="nouveau",
                        )
                        db.add(decouverte)
                        total += 1

                except Exception as e:
                    logger.error(f"Erreur collecte {source.nom} / {mot_cle}: {e}")
                    continue

        await db.commit()
        logger.info(f"Collecte terminee : {total} decouvertes stockees")
        return total


async def run_enrichissement() -> int:
    """
    Etape 2 : enrichit les decouvertes brutes du jour via Claude API.
    Retourne le nombre de decouvertes enrichies.
    """
    async with async_session() as db:
        # Charger les thematiques actives
        stmt_themes = select(Thematique).where(Thematique.actif.is_(True))
        result_themes = await db.execute(stmt_themes)
        thematiques = [t.nom for t in result_themes.scalars().all()]

        # Charger les preferences "ignore" recentes pour le filtrage
        stmt_prefs = (
            select(Preference)
            .where(Preference.type == "ignore")
            .order_by(Preference.created_at.desc())
            .limit(50)
        )
        result_prefs = await db.execute(stmt_prefs)
        preferences_ignorees = []
        for p in result_prefs.scalars().all():
            preferences_ignorees.extend(p.tags_associes)
        preferences_ignorees = list(set(preferences_ignorees))

        # Charger les decouvertes non enrichies du jour
        stmt = (
            select(Decouverte)
            .where(Decouverte.scan_date == date.today())
            .where(Decouverte.resume.is_(None))
        )
        result = await db.execute(stmt)
        decouvertes = result.scalars().all()

        if not decouvertes:
            logger.info("Aucune decouverte a enrichir")
            return 0

        total = 0
        for decouverte in decouvertes:
            try:
                enrichi = await enrichir_decouverte(
                    titre=decouverte.titre,
                    donnees=decouverte.donnees,
                    thematiques=thematiques,
                    preferences_ignorees=preferences_ignorees,
                )

                decouverte.score_pertinence = enrichi["score_pertinence"]
                decouverte.resume = enrichi["resume"]
                decouverte.tags = enrichi["tags"]
                decouverte.mot_cle_suggere = enrichi.get("mot_cle_suggere")
                total += 1

            except Exception as e:
                logger.error(f"Erreur enrichissement decouverte {decouverte.id}: {e}")
                continue

        await db.commit()
        logger.info(f"Enrichissement termine : {total} decouvertes enrichies")
        return total


async def run_scan_complet(mots_cles: list[str] | None = None) -> dict:
    """Lance collecte + enrichissement (utilise par le bouton Rafraichir)."""
    nb_collectes = await run_collecte(mots_cles)
    nb_enrichies = await run_enrichissement()
    return {
        "nb_collectes": nb_collectes,
        "nb_enrichies": nb_enrichies,
        "date": date.today().isoformat(),
    }
