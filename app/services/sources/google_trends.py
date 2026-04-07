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
    Interroge Google Trends via SerpAPI.
    Config attendue : {"engine": "google_trends", "gl": "fr", "hl": "fr"}
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": config.get("engine", "google_trends"),
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

        values = []
        dates = []
        for point in timeline:
            val = point.get("values", [{}])[0].get("extracted_value", 0)
            values.append(val)
            dates.append(point.get("date", ""))

        moyenne = sum(values) / len(values) if values else 0
        derniere_valeur = values[-1] if values else 0
        pic = max(values) if values else 0

        quart = len(values) // 4 if len(values) >= 4 else 1
        debut = sum(values[:quart]) / quart
        fin = sum(values[-quart:]) / quart
        tendance_hausse = min(20, max(0, int((fin - debut) / max(debut, 1) * 50)))

        score_partiel = min(100, int(moyenne * 0.4 + derniere_valeur * 0.4 + tendance_hausse))

        related = data.get("related_queries", {})
        top_queries = []
        if "rising" in related:
            top_queries = [
                {"query": q.get("query", ""), "value": q.get("extracted_value", 0)}
                for q in related["rising"][:5]
            ]

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
            "requetes_associees": top_queries,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google Trends : {mot_cle} ({donnees['tendance']}, {score_partiel}/100)",
            url=f"https://trends.google.com/trends/explore?q={mot_cle}&geo=FR",
            donnees=donnees,
            score_partiel=score_partiel,
        )]

    except httpx.HTTPStatusError as e:
        logger.error(f"SerpAPI HTTP error pour '{mot_cle}': {e.response.status_code}")
        return [SourceResult.error(f"SerpAPI erreur HTTP {e.response.status_code}")]
    except Exception as e:
        logger.error(f"SerpAPI erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]


# Compat Phase 1 — ancien format pour le router niches.py existant
async def fetch_google_trends(mot_cle: str) -> dict:
    """Wrapper de compatibilite Phase 1 → Phase 2."""
    results = await fetch_serpapi_trends(mot_cle, {"engine": "google_trends", "gl": "FR", "hl": "fr"})
    if results:
        r = results[0]
        return {"donnees": r.donnees, "score_partiel": r.score_partiel}
    return {"donnees": {"erreur": "Aucun resultat"}, "score_partiel": 0}
