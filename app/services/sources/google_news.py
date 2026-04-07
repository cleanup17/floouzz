"""Source de signaux : Google News via SerpAPI — actualites du moment."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_news(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les top actualites du moment.
    Pas de mot-cle — on ecoute ce qui fait l'actu.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    # Section specifique si configuree (business, technology, etc.)
    topic = config.get("topic", "TECHNOLOGY")

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_news",
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
        "topic_token": topic,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        news = data.get("news_results", [])

        if not news:
            return []

        results = []
        for n in news[:10]:
            titre = n.get("title", "")
            source_name = n.get("source", {}).get("name", "")
            article_date = n.get("date", "")
            link = n.get("link", "")

            results.append(SourceResult(
                titre=f"{titre}",
                url=link,
                donnees={
                    "titre_original": titre,
                    "source_media": source_name,
                    "date_article": article_date,
                    "topic": topic,
                    "source": "google_news",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=60,
            ))

        return results

    except Exception as e:
        logger.error(f"Google News erreur : {e}")
        return [SourceResult.error(str(e))]
