"""Source de signaux : Google Trends via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_serpapi_trends(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les recherches en forte hausse (trending).
    Le mot_cle est ignore — on cherche ce qui EMERGE, pas ce qu'on connait.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    # Trending searches = ce qui monte en ce moment
    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_trends_trending_now",
        "geo": config.get("gl", "FR"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        trending = data.get("trending_searches", [])

        if not trending:
            return [SourceResult(
                titre="Google Trends : aucune tendance detectee",
                donnees={"erreur": "Pas de donnees trending"},
                score_partiel=0,
            )]

        results = []
        for item in trending[:15]:
            query = item.get("query", "")
            volume = item.get("search_volume", 0)
            articles = item.get("articles", [])

            # Score base sur le volume de recherche
            if volume and isinstance(volume, int):
                if volume >= 100000:
                    score = 90
                elif volume >= 50000:
                    score = 75
                elif volume >= 10000:
                    score = 60
                elif volume >= 5000:
                    score = 45
                else:
                    score = 30
            else:
                score = 50

            article_titles = [a.get("title", "") for a in articles[:3]] if articles else []

            results.append(SourceResult(
                titre=f"Trending : {query}",
                url=f"https://trends.google.com/trends/explore?q={query}&geo=FR",
                donnees={
                    "query": query,
                    "volume": volume,
                    "articles_associes": article_titles,
                    "source": "google_trends_trending",
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score,
            ))

        return results

    except Exception as e:
        logger.error(f"Google Trends Trending erreur : {e}")
        return [SourceResult.error(str(e))]


async def fetch_trends_for_keyword(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Analyse : recherche un mot-cle specifique sur Google Trends.
    Utilise uniquement quand on approfondit une niche.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_trends",
        "q": mot_cle,
        "geo": config.get("gl", "FR"),
        "hl": config.get("hl", "fr"),
        "date": "today 12-m",
        "data_type": "TIMESERIES",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        timeline = data.get("interest_over_time", {}).get("timeline_data", [])

        if not timeline:
            return [SourceResult(
                titre=f"Google Trends : {mot_cle}",
                donnees={"erreur": "Aucune donnee disponible", "mot_cle": mot_cle},
                score_partiel=0,
            )]

        values = [
            point.get("values", [{}])[0].get("extracted_value", 0)
            for point in timeline
        ]
        dates = [point.get("date", "") for point in timeline]

        moyenne = sum(values) / len(values) if values else 0
        derniere_valeur = values[-1] if values else 0
        pic = max(values) if values else 0

        quart = len(values) // 4 if len(values) >= 4 else 1
        debut = sum(values[:quart]) / quart
        fin = sum(values[-quart:]) / quart
        tendance_hausse = min(20, max(0, int((fin - debut) / max(debut, 1) * 50)))

        score_partiel = min(100, int(moyenne * 0.4 + derniere_valeur * 0.4 + tendance_hausse))

        donnees = {
            "mot_cle": mot_cle,
            "periode": "12 derniers mois",
            "geo": config.get("gl", "FR"),
            "moyenne": round(moyenne, 1),
            "derniere_valeur": derniere_valeur,
            "pic": pic,
            "tendance": "hausse" if fin > debut else "baisse" if fin < debut else "stable",
            "variation_pct": round((fin - debut) / max(debut, 1) * 100, 1),
            "series": dict(zip(dates, values)),
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google Trends : {mot_cle} ({donnees['tendance']}, {score_partiel}/100)",
            url=f"https://trends.google.com/trends/explore?q={mot_cle}&geo=FR",
            donnees=donnees,
            score_partiel=score_partiel,
        )]

    except Exception as e:
        logger.error(f"SerpAPI Trends erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]


# Compat Phase 1
async def fetch_google_trends(mot_cle: str) -> dict:
    """Wrapper de compatibilite Phase 1."""
    results = await fetch_trends_for_keyword(mot_cle, {"gl": "FR", "hl": "fr"})
    if results:
        r = results[0]
        return {"donnees": r.donnees, "score_partiel": r.score_partiel}
    return {"donnees": {"erreur": "Aucun resultat"}, "score_partiel": 0}
