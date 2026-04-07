"""Point d'entree de l'application Floouzz."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import async_session
from app.routers import decouvertes, niches, parametres, sources, webhooks
from app.services.seed import seed_sources_par_defaut


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Actions au demarrage et a l'arret de l'application."""
    async with async_session() as db:
        await seed_sources_par_defaut(db)
    yield


app = FastAPI(
    title="Floouzz",
    description="Recherche et veille de niches de marche basee sur des signaux multi-sources.",
    version="0.2.0",
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
