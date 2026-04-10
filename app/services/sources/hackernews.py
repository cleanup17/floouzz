"""Source de signaux : Hacker News via API Algolia (gratuit, sans cle)."""

import logging
from datetime import datetime, timezone

import httpx

from app.services.sources.base import SourceResult
from app.services.traduction import traduire_titres

logger = logging.getLogger(__name__)

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1"


async def fetch_hackernews(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les posts HN en forte traction (front page).
    Le mot_cle est ignore — on ecoute ce qui monte naturellement.
    Filtre les "Show HN" et posts avec beaucoup de points recents.
    """
    try:
        # Recuperer les posts recents les plus populaires
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{HN_ALGOLIA_URL}/search",
                params={
                    "tags": "story",
                    "hitsPerPage": 30,
                    "numericFilters": "points>50",
                },
            )
            response.raise_for_status()
            data = response.json()

        hits = data.get("hits", [])

        if not hits:
            return []

        hits_retenus = hits[:15]

        # Traduction batch EN->FR des titres (HN est quasi 100% anglophone).
        # Un seul appel Claude pour tout le lot, avant transmission au scanner /
        # pipeline_ia qui attendent du francais en entree.
        titres_en = [h.get("title", "") for h in hits_retenus]
        titres_fr = await traduire_titres(titres_en)

        results = []
        for h, titre_original, titre_fr in zip(hits_retenus, titres_en, titres_fr):
            points = h.get("points", 0) or 0
            comments = h.get("num_comments", 0) or 0
            url = h.get("url", "")
            hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID', '')}"

            # Score : posts avec beaucoup de points = sujet qui passionne
            if points >= 500:
                score = 90
            elif points >= 200:
                score = 75
            elif points >= 100:
                score = 60
            else:
                score = 40

            # Bonus Show HN detecte sur le titre original (en anglais)
            is_show_hn = titre_original.lower().startswith("show hn")
            if is_show_hn:
                score = min(100, score + 15)

            results.append(SourceResult(
                titre=titre_fr,
                url=hn_url,
                donnees={
                    "titre_original": titre_original,
                    "titre_fr": titre_fr,
                    "points": points,
                    "commentaires": comments,
                    "url_externe": url,
                    "show_hn": is_show_hn,
                    "source": "hackernews",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score,
            ))

        return results

    except Exception as e:
        logger.error(f"Hacker News erreur : {e}")
        return [SourceResult.error(str(e))]
