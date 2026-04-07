"""Routes du mode Decouverte — dashboard de signaux quotidiens."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Decouverte, Preference, Thematique
from app.services.scanner import run_scan_complet

router = APIRouter(prefix="/decouverte", tags=["decouverte"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def page_decouverte(
    request: Request,
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard des signaux du jour."""
    stmt = (
        select(Decouverte)
        .where(Decouverte.scan_date == date.today())
        .where(Decouverte.statut != "ignore")
        .order_by(Decouverte.score_pertinence.desc().nulls_last())
    )
    if tag:
        stmt = stmt.where(Decouverte.tags.any(tag))

    result = await db.execute(stmt)
    signaux = result.scalars().all()

    # Filtrer ceux avec score >= 30 (ou sans score encore)
    signaux_filtres = [s for s in signaux if s.score_pertinence is None or s.score_pertinence >= 30]

    # Compter les signaux par tag
    tags_count: dict[str, int] = {}
    for s in signaux_filtres:
        if s.tags:
            for t in s.tags:
                tags_count[t] = tags_count.get(t, 0) + 1

    # Thematiques configurees
    stmt_themes = select(Thematique).where(Thematique.actif.is_(True)).order_by(Thematique.nom)
    result_themes = await db.execute(stmt_themes)
    thematiques = result_themes.scalars().all()

    # Stats
    stmt_total = (
        select(func.count(Decouverte.id))
        .where(Decouverte.scan_date == date.today())
    )
    result_total = await db.execute(stmt_total)
    total_signaux = result_total.scalar() or 0

    return templates.TemplateResponse("decouverte.html", {
        "request": request,
        "signaux": signaux_filtres,
        "tags_count": tags_count,
        "thematiques": thematiques,
        "tag_actif": tag,
        "total_signaux": total_signaux,
        "nb_affiches": len(signaux_filtres),
        "date_scan": date.today(),
    })


@router.post("/rafraichir", response_class=HTMLResponse)
async def rafraichir(request: Request, db: AsyncSession = Depends(get_db)):
    """Relance un scan complet (collecte + enrichissement)."""
    await run_scan_complet()
    return await page_decouverte(request, tag=None, db=db)


@router.post("/ignorer/{decouverte_id}", response_class=HTMLResponse)
async def ignorer_signal(
    request: Request,
    decouverte_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Marque un signal comme ignore et enregistre la preference."""
    stmt = select(Decouverte).where(Decouverte.id == decouverte_id)
    result = await db.execute(stmt)
    decouverte = result.scalar_one_or_none()

    if decouverte:
        decouverte.statut = "ignore"
        pref = Preference(
            type="ignore",
            decouverte_id=decouverte.id,
            tags_associes=decouverte.tags or [],
        )
        db.add(pref)
        await db.commit()

    return HTMLResponse("")


@router.post("/approfondir/{decouverte_id}", response_class=HTMLResponse)
async def approfondir_signal(
    request: Request,
    decouverte_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Approfondir un signal — redirige vers l'analyse avec le mot-cle."""
    stmt = select(Decouverte).where(Decouverte.id == decouverte_id)
    result = await db.execute(stmt)
    decouverte = result.scalar_one_or_none()

    if not decouverte:
        return templates.TemplateResponse("partials/erreur.html", {
            "request": request,
            "message": "Signal introuvable.",
        })

    mot_cle = decouverte.mot_cle_suggere or decouverte.titre[:100]
    decouverte.statut = "approfondi"

    pref = Preference(
        type="like",
        decouverte_id=decouverte.id,
        tags_associes=decouverte.tags or [],
    )
    db.add(pref)
    await db.commit()

    return HTMLResponse(
        f'<script>window.location.href="/analyser?mot_cle={mot_cle}";</script>'
    )
