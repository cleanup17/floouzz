"""Routes CRUD pour l'admin des sources."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Source
from app.schemas import SourceCreate
from app.services.sources.base import fetch_source

router = APIRouter(prefix="/api/sources", tags=["sources"])
templates = Jinja2Templates(directory="app/templates")


@router.post("/", response_class=HTMLResponse)
async def creer_source(request: Request, db: AsyncSession = Depends(get_db)):
    """Cree une nouvelle source."""
    form = await request.form()
    config_str = form.get("config", "{}")
    try:
        config = json.loads(config_str) if config_str else {}
    except json.JSONDecodeError:
        config = {}

    source_data = SourceCreate(
        nom=form.get("nom", ""),
        type=form.get("type", "serpapi"),
        config=config,
        cle_api_ref=form.get("cle_api_ref") or None,
        actif=form.get("actif") == "on",
        cron_expr=form.get("cron_expr", "0 6 * * *"),
    )
    source = Source(**source_data.model_dump())
    db.add(source)
    await db.commit()

    # Recharger la page parametres
    return HTMLResponse(
        '<script>window.location.href="/parametres/";</script>'
    )


@router.post("/{source_id}/toggle", response_class=HTMLResponse)
async def toggle_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Active ou desactive une source."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source:
        source.actif = not source.actif
        source.updated_at = datetime.now(timezone.utc)
        await db.commit()

    return HTMLResponse(
        '<script>window.location.href="/parametres/";</script>'
    )


@router.delete("/{source_id}", response_class=HTMLResponse)
async def supprimer_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Supprime une source."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source:
        await db.delete(source)
        await db.commit()

    return HTMLResponse(
        '<script>window.location.href="/parametres/";</script>'
    )


@router.post("/{source_id}/tester", response_class=HTMLResponse)
async def tester_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Teste une source en lancant un appel reel."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if not source:
        return HTMLResponse("<p class='text-red-400'>Source introuvable</p>")

    results = await fetch_source(source.type, "test", source.config)
    html = "<div class='bg-gray-800 rounded p-3 mt-2 text-sm space-y-1'>"
    for r in results:
        if r.donnees.get("erreur"):
            html += f"<p class='text-red-400'>Erreur : {r.donnees['erreur']}</p>"
        else:
            html += f"<p class='text-green-400'>{r.titre} — score: {r.score_partiel}</p>"
    html += "</div>"

    return HTMLResponse(html)
