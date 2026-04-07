"""Insertion des sources par defaut au premier demarrage."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = [
    # --- Gratuit (sans cle) ---
    {
        "nom": "Hacker News — posts populaires",
        "type": "api",
        "config": {"fetcher": "hackernews"},
        "cle_api_ref": None,
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- SerpAPI (1 appel chacune) ---
    {
        "nom": "Google Trends — ce qui monte en France",
        "type": "serpapi",
        "config": {"engine": "google_trends", "gl": "FR", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News — actu tech",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr", "topic": "TECHNOLOGY"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News — actu business",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr", "topic": "BUSINESS"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Jobs — metiers qui recrutent",
        "type": "serpapi",
        "config": {
            "engine": "google_jobs", "gl": "fr", "hl": "fr",
            "queries": ["developpeur IA", "automatisation", "e-commerce manager", "data analyst", "no-code"],
        },
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- Apify ---
    {
        "nom": "Reddit — posts chauds communautes tech/business",
        "type": "apify_actor",
        "config": {
            "actor_id": "trudax/reddit-scraper",
            "input": {
                "subreddits": ["SaaS", "smallbusiness", "startups", "Entrepreneur", "artificial", "ecommerce", "nocode"],
                "sort": "hot",
                "time": "week",
                "maxItems": 50,
            },
        },
        "cle_api_ref": "APIFY_TOKEN",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Product Hunt — produits du jour",
        "type": "apify_actor",
        "config": {
            "actor_id": "dainty_screw/producthunt-scraper",
            "fetcher": "producthunt",
            "input": {"listType": "today", "maxItems": 15},
        },
        "cle_api_ref": "APIFY_TOKEN",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
]


async def seed_sources_par_defaut(db: AsyncSession) -> int:
    """Insere les sources par defaut si la table est vide."""
    stmt = select(Source).limit(1)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        logger.info("Sources deja presentes — seed ignore")
        return 0

    count = 0
    for s in DEFAULT_SOURCES:
        source = Source(**s)
        db.add(source)
        count += 1

    await db.commit()
    logger.info(f"Seed : {count} sources par defaut inserees")
    return count
