"""Point d'entree de l'application Floouzz."""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from sqlalchemy import text

from app.database import async_session
from app.routers import decouvertes, exports, niches, parametres, sources, webhooks
from app.services.scanner import run_scan_complet
from app.services.seed import (
    seed_sources_manquantes,
    seed_sources_par_defaut,
    seed_thematiques_manquantes,
)

# ---------------------------------------------------------------------------
# Filtre de logging pour masquer les cles API dans les URLs httpx.
# httpx logge les requetes au niveau INFO avec l'URL complete incluant
# les query params (api_key=XXX, token=XXX). Ce filtre remplace les valeurs
# sensibles par "***" avant qu'elles arrivent dans la sortie.
# ---------------------------------------------------------------------------

import re

_SECRETS_PATTERN = re.compile(
    r"(api_key|token|key|secret|password)=([^\s&\"']+)",
    re.IGNORECASE,
)


class _SecretFilter(logging.Filter):
    """Masque les cles API dans les messages de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            # Les args peuvent etre un tuple ou un dict
            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(
                    _SECRETS_PATTERN.sub(r"\1=***", str(a)) if isinstance(a, str) else a
                    for a in args
                )
            elif isinstance(args, dict):
                record.args = {
                    k: _SECRETS_PATTERN.sub(r"\1=***", str(v)) if isinstance(v, str) else v
                    for k, v in args.items()
                }
        if isinstance(record.msg, str):
            record.msg = _SECRETS_PATTERN.sub(r"\1=***", record.msg)
        return True


# Configuration minimale du logging pour que les logger.info() applicatifs
# remontent dans la sortie uvicorn. Sans cela, le root logger filtre les INFO.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Appliquer le filtre sur le root logger et sur httpx specifiquement
logging.getLogger().addFilter(_SecretFilter())
logging.getLogger("httpx").addFilter(_SecretFilter())

logger = logging.getLogger(__name__)


def _creer_scheduler() -> AsyncIOScheduler | None:
    """
    Cree et configure le scheduler du scan automatique.

    Lit settings.SCAN_CRON (cron a 5 champs) et planifie run_scan_complet.
    Retourne None si SCAN_CRON est vide (scheduler desactive) ou si la
    valeur est invalide.
    """
    if not settings.SCAN_CRON or not settings.SCAN_CRON.strip():
        logger.info("Scheduler desactive (SCAN_CRON vide)")
        return None

    try:
        trigger = CronTrigger.from_crontab(settings.SCAN_CRON)
    except ValueError as e:
        logger.error(f"Scheduler : SCAN_CRON invalide '{settings.SCAN_CRON}' ({e})")
        return None

    scheduler = AsyncIOScheduler(timezone="Europe/Paris")
    scheduler.add_job(
        run_scan_complet,
        trigger=trigger,
        id="scan_quotidien",
        name="Scan Floouzz quotidien",
        replace_existing=True,
        max_instances=1,  # Pas de chevauchement si un scan deborde
        coalesce=True,    # Si on a rate plusieurs declenchements, un seul rattrapage
    )
    logger.info(f"Scheduler configure : SCAN_CRON='{settings.SCAN_CRON}'")
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Actions au demarrage et a l'arret de l'application."""
    # Seed des sources par defaut (premier demarrage uniquement)
    async with async_session() as db:
        await seed_sources_par_defaut(db)

    # Seed des sources manquantes ajoutees apres le premier demarrage
    # (ex: Sitemap Intelligence en v0.4+). Idempotent, safe a chaque boot.
    async with async_session() as db:
        await seed_sources_manquantes(db)

    # Seed des thematiques de reference (51 categories FR).
    # Idempotent : ajoute uniquement les thematiques manquantes, preserve
    # les custom ajoutees manuellement par l'utilisatrice via /parametres/.
    async with async_session() as db:
        await seed_thematiques_manquantes(db)

    # Demarrage du scheduler (si configure)
    scheduler = _creer_scheduler()
    if scheduler is not None:
        scheduler.start()
        logger.info("Scheduler demarre")

    yield

    # Arret propre du scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler arrete")


app = FastAPI(
    title="Floouzz",
    description="Recherche et veille de niches de marche basee sur des signaux multi-sources.",
    version="0.6.0",
    lifespan=lifespan,
)

# Fichiers statiques
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routes
app.include_router(decouvertes.router)
app.include_router(niches.router)
app.include_router(sources.router)
app.include_router(parametres.router)
app.include_router(webhooks.router)
app.include_router(exports.router)


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health():
    """
    Healthcheck endpoint pour monitoring Docker / probes k8s.
    Verifie la connexion BDD avec un SELECT 1.
    """
    db_status = "ok"
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    status = "ok" if db_status == "ok" else "degraded"
    return {
        "status": status,
        "version": app.version,
        "db": db_status,
    }
