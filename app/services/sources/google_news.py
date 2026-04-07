"""Source de signaux : Google News via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_news(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google News via SerpAPI.
    Retourne les articles recents et un score d'actualite.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_news",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        news = data.get("news_results", [])
        nb_articles = len(news)

        if nb_articles >= 10:
            score = 90
        elif nb_articles >= 5:
            score = 65
        elif nb_articles >= 2:
            score = 40
        elif nb_articles >= 1:
            score = 20
        else:
            score = 0

        top_articles = [
            {
                "titre": n.get("title", ""),
                "source": n.get("source", {}).get("name", ""),
                "date": n.get("date", ""),
                "lien": n.get("link", ""),
            }
            for n in news[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_articles": nb_articles,
            "top_articles": top_articles,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google News : {nb_articles} articles pour '{mot_cle}'",
            url=f"https://news.google.com/search?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Google News erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
