"""Orchestrateur du scan quotidien — collecte + enrichissement."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Decouverte, Preference, Source, Thematique
from app.services.enrichissement import enrichir_batch, MAX_SIGNAUX_PAR_BATCH
from app.services.sources.base import fetch_source

logger = logging.getLogger(__name__)


async def run_collecte() -> int:
    """
    Etape 1 : collecte des signaux depuis toutes les sources actives.
    Chaque source ecoute ce qui monte — pas de mots-cles imposes.
    Retourne le nombre de decouvertes stockees.
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
            try:
                # Determiner le type de fetcher
                source_type = source.type
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

                # Chaque source ecoute ce qui monte — pas de mot-cle
                results = await fetch_source(source_type, "", source.config)

                for r in results:
                    # Ignorer les erreurs
                    if r.donnees.get("erreur"):
                        logger.warning(f"Erreur source {source.nom}: {r.donnees['erreur']}")
                        continue

                    decouverte = Decouverte(
                        source_id=source.id,
                        titre=r.titre,
                        url=r.url,
                        donnees=r.donnees,
                        scan_date=date.today(),
                        statut="nouveau",
                    )
                    db.add(decouverte)
                    total += 1

            except Exception as e:
                logger.error(f"Erreur collecte {source.nom}: {e}")
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
        stmt_themes = select(Thematique).where(Thematique.actif.is_(True))
        result_themes = await db.execute(stmt_themes)
        thematiques = [t.nom for t in result_themes.scalars().all()]

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

        # Limiter a 30 signaux max (les plus recents)
        decouvertes = decouvertes[:30]

        # Enrichir par batch de MAX_SIGNAUX_PAR_BATCH
        total = 0
        for i in range(0, len(decouvertes), MAX_SIGNAUX_PAR_BATCH):
            batch = decouvertes[i:i + MAX_SIGNAUX_PAR_BATCH]
            signaux_data = [{"titre": d.titre, "donnees": d.donnees} for d in batch]

            try:
                enrichis = await enrichir_batch(
                    signaux_data,
                    thematiques,
                    preferences_ignorees,
                )

                for decouverte, enrichi in zip(batch, enrichis):
                    decouverte.score_pertinence = enrichi["score_pertinence"]
                    decouverte.resume = enrichi["resume"]
                    decouverte.tags = enrichi["tags"]
                    decouverte.mot_cle_suggere = enrichi.get("mot_cle_suggere")
                    total += 1

            except Exception as e:
                logger.error(f"Erreur enrichissement batch : {e}")
                continue

        await db.commit()
        logger.info(f"Enrichissement termine : {total} decouvertes enrichies")
        return total


async def run_scan_complet() -> dict:
    """Lance collecte + enrichissement (utilise par le bouton Rafraichir)."""
    nb_collectes = await run_collecte()
    nb_enrichies = await run_enrichissement()
    return {
        "nb_collectes": nb_collectes,
        "nb_enrichies": nb_enrichies,
        "date": date.today().isoformat(),
    }
