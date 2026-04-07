"""Insertion des sources par defaut au premier demarrage."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = [
    {
        "nom": "Google Trends FR",
        "type": "serpapi",
        "config": {"engine": "google_trends", "gl": "FR", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Jobs FR",
        "type": "serpapi",
        "config": {"engine": "google_jobs", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Search (CPC + PAA)",
        "type": "serpapi",
        "config": {"engine": "google", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News FR",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Reddit",
        "type": "apify_actor",
        "config": {
            "actor_id": "trudax/reddit-scraper",
            "input": {
                "subreddits": ["SaaS", "smallbusiness", "startups", "Entrepreneur", "artificial"],
                "sort": "hot",
                "time": "week",
                "maxItems": 30,
            },
        },
        "cle_api_ref": "APIFY_TOKEN",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Hacker News",
        "type": "api",
        "config": {"fetcher": "hackernews"},
        "cle_api_ref": None,
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
