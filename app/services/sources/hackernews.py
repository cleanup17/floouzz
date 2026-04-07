"""Source de signaux : Hacker News via API Algolia (gratuit, sans cle)."""

import logging
from datetime import datetime, timezone

import httpx

from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1"


async def fetch_hackernews(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Hacker News via l'API Algolia (gratuit, sans cle).
    Retourne les posts recents sur le sujet.
    """
    params = {
        "query": mot_cle,
        "tags": "story",
        "hitsPerPage": 30,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{HN_ALGOLIA_URL}/search_by_date", params=params)
            response.raise_for_status()
            data = response.json()

        hits = data.get("hits", [])
        nb_hits = data.get("nbHits", 0)

        if not hits:
            return [SourceResult(
                titre=f"Hacker News : aucun post pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "nb_posts": 0},
                score_partiel=0,
            )]

        total_points = sum(h.get("points", 0) or 0 for h in hits)
        total_comments = sum(h.get("num_comments", 0) or 0 for h in hits)

        if total_points >= 500:
            score = 90
        elif total_points >= 200:
            score = 70
        elif total_points >= 50:
            score = 50
        elif total_points >= 10:
            score = 30
        else:
            score = 10

        top_posts = [
            {
                "titre": h.get("title", ""),
                "points": h.get("points", 0),
                "commentaires": h.get("num_comments", 0),
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
                "date": h.get("created_at", ""),
            }
            for h in sorted(hits, key=lambda x: x.get("points", 0) or 0, reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_posts": nb_hits,
            "total_points": total_points,
            "total_commentaires": total_comments,
            "top_posts": top_posts,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Hacker News : {nb_hits} posts, {total_points} points pour '{mot_cle}'",
            url=f"https://hn.algolia.com/?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Hacker News erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
