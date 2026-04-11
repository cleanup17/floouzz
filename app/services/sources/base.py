"""Interface commune pour toutes les sources de signaux."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class SourceResult:
    """Resultat standardise d'un appel a une source."""

    titre: str
    url: str | None = None
    donnees: dict = field(default_factory=dict)
    score_partiel: int = 0

    @classmethod
    def error(cls, message: str) -> SourceResult:
        """Cree un resultat d'erreur."""
        return cls(
            titre="Erreur",
            donnees={"erreur": message},
            score_partiel=0,
        )


# Type d'un fetcher : prend (mot_cle, config) et retourne une liste de SourceResult
FetcherType = Callable[[str, dict], Coroutine[None, None, list[SourceResult]]]

# Registre des fetchers par type de source
_FETCHERS: dict[str, FetcherType] = {}


def register_fetcher(source_type: str, fetcher: FetcherType) -> None:
    """Enregistre un fetcher pour un type de source."""
    _FETCHERS[source_type] = fetcher


def get_source_fetcher(source_type: str) -> FetcherType | None:
    """Retourne le fetcher pour un type de source donne."""
    _ensure_registered()
    return _FETCHERS.get(source_type)


async def fetch_source(source_type: str, mot_cle: str, config: dict) -> list[SourceResult]:
    """Appelle le fetcher correspondant au type de source."""
    fetcher = get_source_fetcher(source_type)
    if fetcher is None:
        logger.warning(f"Type de source inconnu : {source_type}")
        return [SourceResult.error(f"Type de source inconnu : {source_type}")]
    try:
        return await fetcher(mot_cle, config)
    except Exception as e:
        logger.error(f"Erreur source {source_type} pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]


_registered = False


def _ensure_registered() -> None:
    """Enregistre les fetchers au premier appel (evite les imports circulaires)."""
    global _registered
    if _registered:
        return
    _registered = True

    from app.services.sources.google_trends import fetch_serpapi_trends
    from app.services.sources.google_trends_rss import fetch_google_trends_rss
    from app.services.sources.google_jobs import fetch_google_jobs
    from app.services.sources.google_search import fetch_google_search
    from app.services.sources.google_news import fetch_google_news
    from app.services.sources.reddit import fetch_reddit
    from app.services.sources.producthunt import fetch_producthunt
    from app.services.sources.hackernews import fetch_hackernews
    from app.services.sources.apify_url import fetch_apify_url
    from app.services.sources.sitemap import fetch_sitemap

    register_fetcher("serpapi", fetch_serpapi_trends)
    register_fetcher("google_trends_rss", fetch_google_trends_rss)
    register_fetcher("serpapi_jobs", fetch_google_jobs)
    register_fetcher("serpapi_search", fetch_google_search)
    register_fetcher("serpapi_news", fetch_google_news)
    register_fetcher("apify_actor", fetch_reddit)
    register_fetcher("producthunt", fetch_producthunt)
    register_fetcher("hackernews", fetch_hackernews)
    register_fetcher("apify_url", fetch_apify_url)
    register_fetcher("sitemap", fetch_sitemap)
