"""Point d'entrée de l'application Floouzz."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import niches

app = FastAPI(
    title="Floouzz",
    description="Recherche et veille de niches de marché basée sur des signaux multi-sources.",
    version="0.1.0",
)

# Fichiers statiques
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routes
app.include_router(niches.router)
