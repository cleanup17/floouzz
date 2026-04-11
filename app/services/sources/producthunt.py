"""Source de signaux : Product Hunt via Apify — produits lances du jour."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult
from app.services.traduction import traduire_titres

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_producthunt(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les produits les plus votes aujourd'hui.
    Pas de mot-cle — on ecoute ce qui est lance et populaire.
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "dainty_screw/producthunt-scraper")
    actor_input = {
        "listType": "today",
        "maxItems": 15,
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

        if not products:
            return []

        produits_retenus = sorted(
            products, key=lambda x: x.get("votesCount", 0), reverse=True
        )[:10]

        # Traduction batch EN->FR des taglines (les noms de produits sont des
        # marques, on ne les traduit pas — que les descriptifs). Un seul appel
        # Claude pour le lot, avant transmission au scanner / pipeline_ia.
        taglines_en = [p.get("tagline", "") for p in produits_retenus]
        taglines_fr = await traduire_titres(taglines_en)

        results = []
        for p, tagline_en, tagline_fr in zip(produits_retenus, taglines_en, taglines_fr):
            nom = p.get("name", "")
            votes = p.get("votesCount", 0)
            product_url = p.get("url", "")

            if votes >= 200:
                score = 90
            elif votes >= 100:
                score = 75
            elif votes >= 50:
                score = 60
            else:
                score = 40

            results.append(SourceResult(
                titre=f"PH : {nom} — {tagline_fr}",
                url=product_url,
                donnees={
                    "nom": nom,
                    "tagline_original": tagline_en,
                    "tagline_fr": tagline_fr,
                    "votes": votes,
                    "source": "producthunt",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score,
            ))

        return results

    except Exception as e:
        logger.error(f"Product Hunt erreur : {e}")
        return [SourceResult.error(str(e))]
