"""Source de signaux : Product Hunt via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_producthunt(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Product Hunt via Apify actor.
    Retourne les produits existants dans la niche — signal concurrence.
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "dainty_screw/producthunt-scraper")
    actor_input = {
        "search": mot_cle,
        "maxItems": 20,
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
            products = response.json()

        nb_products = len(products)

        # Score concurrence inverse : beaucoup de produits = mauvais score
        if nb_products >= 20:
            score = 15
        elif nb_products >= 10:
            score = 35
        elif nb_products >= 5:
            score = 55
        elif nb_products >= 1:
            score = 75
        else:
            score = 95

        top_products = [
            {
                "nom": p.get("name", ""),
                "tagline": p.get("tagline", ""),
                "votes": p.get("votesCount", 0),
                "url": p.get("url", ""),
            }
            for p in sorted(products, key=lambda x: x.get("votesCount", 0), reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_produits": nb_products,
            "top_produits": top_products,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Product Hunt : {nb_products} produits pour '{mot_cle}'",
            url=f"https://www.producthunt.com/search?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Product Hunt erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
