"""Source de signaux : Google Search via SerpAPI — SERP, CPC, People Also Ask."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_search(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google Search via SerpAPI.
    Extrait : nombre de resultats, CPC des ads, People Also Ask.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        results = []

        # People Also Ask — signal douleur
        paa = data.get("related_questions", [])
        if paa:
            questions = [q.get("question", "") for q in paa[:5]]
            results.append(SourceResult(
                titre=f"People Also Ask : {len(paa)} questions pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "questions": questions,
                    "nb_questions": len(paa),
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=min(100, len(paa) * 15),
            ))

        # Ads — signal monetisation
        ads = data.get("ads", [])
        nb_ads = len(ads)
        if nb_ads > 0:
            top_ads = [
                {"titre": a.get("title", ""), "lien": a.get("displayed_link", "")}
                for a in ads[:3]
            ]
            score_ads = min(100, nb_ads * 20)
            results.append(SourceResult(
                titre=f"Google Ads : {nb_ads} annonces pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "nb_ads": nb_ads,
                    "top_ads": top_ads,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score_ads,
            ))

        # Nombre total de resultats — signal concurrence
        search_info = data.get("search_information", {})
        total_results = search_info.get("total_results", 0)
        if total_results:
            # Moins de resultats = moins de concurrence = meilleur score
            if total_results < 100_000:
                score_conc = 90
            elif total_results < 1_000_000:
                score_conc = 70
            elif total_results < 10_000_000:
                score_conc = 50
            elif total_results < 100_000_000:
                score_conc = 30
            else:
                score_conc = 10

            results.append(SourceResult(
                titre=f"Google : {total_results:,} resultats pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "total_resultats": total_results,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score_conc,
            ))

        if not results:
            results.append(SourceResult(
                titre=f"Google Search : pas de donnees exploitables pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle},
                score_partiel=0,
            ))

        return results

    except Exception as e:
        logger.error(f"Google Search erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
