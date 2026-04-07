"""Source de signaux : Google Jobs via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_jobs(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google Jobs via SerpAPI.
    Retourne le nombre d'offres et les principales.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_jobs",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        jobs = data.get("jobs_results", [])
        nb_jobs = len(jobs)

        if nb_jobs >= 30:
            score = 100
        elif nb_jobs >= 15:
            score = 75
        elif nb_jobs >= 5:
            score = 50
        elif nb_jobs >= 1:
            score = 25
        else:
            score = 0

        top_jobs = [
            {
                "titre": j.get("title", ""),
                "entreprise": j.get("company_name", ""),
                "lieu": j.get("location", ""),
            }
            for j in jobs[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_offres": nb_jobs,
            "top_offres": top_jobs,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google Jobs : {nb_jobs} offres pour '{mot_cle}'",
            url=f"https://www.google.com/search?q={mot_cle}&ibp=htl;jobs",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Google Jobs erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
