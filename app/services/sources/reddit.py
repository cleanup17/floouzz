"""Source de signaux : Reddit via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_reddit(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : scanner les posts chauds de subreddits specifiques.
    Le mot_cle est ignore — on ecoute ce qui monte dans les communautes.
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "trudax/reddit-scraper")
    subreddits = config.get("input", {}).get("subreddits", [
        "SaaS", "smallbusiness", "startups", "Entrepreneur", "artificial",
    ])

    actor_input = {
        "startUrls": [{"url": f"https://www.reddit.com/r/{sub}/hot/"} for sub in subreddits],
        "sort": "hot",
        "maxItems": 50,
        "maxPostCount": 50,
        "proxy": {"useApifyProxy": True},
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
            posts = response.json()

        if not posts:
            return []

        # Filtrer les posts interessants (beaucoup de commentaires = discussion active)
        results = []
        for p in sorted(posts, key=lambda x: x.get("numberOfComments", 0), reverse=True)[:15]:
            titre = p.get("title", "")
            comments = p.get("numberOfComments", 0)
            upvotes = p.get("upVotes", 0)
            subreddit = p.get("subreddit", "")
            post_url = p.get("url", "")

            # On garde que les posts avec un minimum d'engagement
            if comments < 5 and upvotes < 20:
                continue

            if comments >= 100:
                score = 85
            elif comments >= 50:
                score = 70
            elif comments >= 20:
                score = 55
            else:
                score = 40

            results.append(SourceResult(
                titre=f"r/{subreddit} : {titre}",
                url=post_url,
                donnees={
                    "titre_original": titre,
                    "subreddit": subreddit,
                    "upvotes": upvotes,
                    "commentaires": comments,
                    "source": "reddit",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score,
            ))

        return results

    except Exception as e:
        logger.error(f"Reddit erreur : {e}")
        return [SourceResult.error(str(e))]
