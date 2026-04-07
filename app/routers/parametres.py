"""Routes pour la page parametres."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Decouverte, Source, Thematique

router = APIRouter(prefix="/parametres", tags=["parametres"])
templates = Jinja2Templates(directory="app/templates")


def _masquer_cle(cle: str | None) -> str:
    """Masque une cle API : affiche les 4 premiers et 4 derniers caracteres."""
    if not cle:
        return "Non configuree"
    if len(cle) <= 8:
        return "****"
    return f"{cle[:4]}...{cle[-4:]}"


@router.get("/", response_class=HTMLResponse)
async def page_parametres(request: Request, db: AsyncSession = Depends(get_db)):
    """Page parametres : cles API, sources, thematiques, stats scan."""
    cles_api = [
        {"nom": "SERPAPI_KEY", "masquee": _masquer_cle(settings.SERPAPI_KEY), "configuree": bool(settings.SERPAPI_KEY)},
        {"nom": "APIFY_TOKEN", "masquee": _masquer_cle(settings.APIFY_TOKEN), "configuree": bool(settings.APIFY_TOKEN)},
        {"nom": "ANTHROPIC_API_KEY", "masquee": _masquer_cle(settings.ANTHROPIC_API_KEY), "configuree": bool(settings.ANTHROPIC_API_KEY)},
    ]

    stmt_sources = select(Source).order_by(Source.created_at)
    result_sources = await db.execute(stmt_sources)
    sources = result_sources.scalars().all()

    stmt_themes = select(Thematique).order_by(Thematique.nom)
    result_themes = await db.execute(stmt_themes)
    thematiques = result_themes.scalars().all()

    stmt_stats = select(func.count(Decouverte.id)).where(
        Decouverte.scan_date == func.current_date()
    )
    result_stats = await db.execute(stmt_stats)
    nb_scan_jour = result_stats.scalar() or 0

    return templates.TemplateResponse("parametres.html", {
        "request": request,
        "cles_api": cles_api,
        "sources": sources,
        "thematiques": thematiques,
        "nb_scan_jour": nb_scan_jour,
    })


@router.post("/thematiques", response_class=HTMLResponse)
async def ajouter_thematique(request: Request, db: AsyncSession = Depends(get_db)):
    """Ajoute une thematique."""
    form = await request.form()
    nom = form.get("nom", "").strip()
    if nom and len(nom) >= 2:
        theme = Thematique(nom=nom)
        db.add(theme)
        await db.commit()
    return HTMLResponse(
        '<script>window.location.href="/parametres/";</script>'
    )


@router.delete("/thematiques/{theme_id}", response_class=HTMLResponse)
async def supprimer_thematique(
    request: Request,
    theme_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Supprime une thematique."""
    stmt = select(Thematique).where(Thematique.id == theme_id)
    result = await db.execute(stmt)
    theme = result.scalar_one_or_none()
    if theme:
        await db.delete(theme)
        await db.commit()
    return HTMLResponse(
        '<script>window.location.href="/parametres/";</script>'
    )
