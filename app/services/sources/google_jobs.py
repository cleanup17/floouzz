"""Source de signaux : Google Jobs via SerpAPI — metiers qui recrutent."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_jobs(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : scanner les offres d'emploi par categorie.
    Utilise des requetes larges par domaine pour detecter ou ca recrute.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    # Requetes par domaine — on detecte ou ca embauche
    queries = config.get("queries", [
        "développeur IA",
        "automatisation",
        "e-commerce manager",
        "data analyst",
        "no-code",
    ])

    results = []
    for query in queries[:5]:
        params = {
            "api_key": settings.SERPAPI_KEY,
            "engine": "google_jobs",
            "q": query,
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

            if nb_jobs == 0:
                continue

            top_jobs = [
                {
                    "titre": j.get("title", ""),
                    "entreprise": j.get("company_name", ""),
                    "lieu": j.get("location", ""),
                }
                for j in jobs[:3]
            ]

            results.append(SourceResult(
                titre=f"Emploi : {nb_jobs}+ offres '{query}'",
                url=f"https://www.google.com/search?q={query}&ibp=htl;jobs",
                donnees={
                    "requete": query,
                    "nb_offres": nb_jobs,
                    "top_offres": top_jobs,
                    "source": "google_jobs",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=min(100, nb_jobs * 5),
            ))

        except Exception as e:
            logger.error(f"Google Jobs erreur pour '{query}': {e}")
            continue

    return results
