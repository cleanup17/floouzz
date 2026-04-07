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
    Interroge Reddit via Apify actor.
    Config attendue : {"actor_id": "trudax/reddit-scraper", "input": {"subreddits": [...]}}
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "trudax/reddit-scraper")
    default_input = {
        "searchPosts": True,
        "searches": [mot_cle],
        "sort": "hot",
        "time": "week",
        "maxItems": 30,
    }
    actor_input = {**default_input, **config.get("input", {})}
    actor_input["searches"] = [mot_cle]

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
            return [SourceResult(
                titre=f"Reddit : aucun post pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "nb_posts": 0},
                score_partiel=0,
            )]

        nb_posts = len(posts)
        total_comments = sum(p.get("numberOfComments", 0) for p in posts)
        total_upvotes = sum(p.get("upVotes", 0) for p in posts)

        if total_comments >= 500:
            score = 95
        elif total_comments >= 200:
            score = 75
        elif total_comments >= 50:
            score = 55
        elif total_comments >= 10:
            score = 30
        else:
            score = 10

        top_posts = [
            {
                "titre": p.get("title", ""),
                "subreddit": p.get("subreddit", ""),
                "upvotes": p.get("upVotes", 0),
                "commentaires": p.get("numberOfComments", 0),
                "url": p.get("url", ""),
            }
            for p in sorted(posts, key=lambda x: x.get("numberOfComments", 0), reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_posts": nb_posts,
            "total_commentaires": total_comments,
            "total_upvotes": total_upvotes,
            "top_posts": top_posts,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Reddit : {nb_posts} posts, {total_comments} commentaires pour '{mot_cle}'",
            url=f"https://www.reddit.com/search/?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Reddit erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
