"""Source de signaux : scraper URL generique via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_apify_url(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Scrape une ou plusieurs URLs via Apify Web Scraper.
    Config attendue : {"urls": ["https://..."], "actor_id": "apify/web-scraper"}
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    urls = config.get("urls", [])
    if not urls:
        return [SourceResult.error("Aucune URL configuree pour cette source")]

    actor_id = config.get("actor_id", "apify/web-scraper")
    actor_input = {
        "startUrls": [{"url": u} for u in urls],
        "maxPagesPerCrawl": config.get("max_pages", 10),
        **config.get("input", {}),
    }

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"token": settings.APIFY_TOKEN},
                json=actor_input,
            )
            response.raise_for_status()
            items = response.json()

        if not items:
            return [SourceResult(
                titre=f"URL scraper : aucun resultat pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "urls": urls},
                score_partiel=0,
            )]

        results = []
        for item in items[:10]:
            titre = item.get("title", item.get("text", "Sans titre"))[:200]
            results.append(SourceResult(
                titre=titre,
                url=item.get("url", urls[0] if urls else None),
                donnees={
                    "mot_cle": mot_cle,
                    "contenu": item,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=50,
            ))

        return results

    except Exception as e:
        logger.error(f"Apify URL scraper erreur : {e}")
        return [SourceResult.error(str(e))]
