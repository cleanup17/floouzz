"""Routes pour l'analyse et la consultation des niches."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Analyse, Niche, Signal
from app.schemas import NicheCreate
from app.services.scoring import calculer_score_global
from app.services.sources.google_trends import fetch_google_trends

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def page_accueil(request: Request, db: AsyncSession = Depends(get_db)):
    """Page d'accueil avec formulaire de recherche et liste des niches récentes."""
    # Récupérer les niches récentes avec le nombre d'analyses
    stmt = (
        select(Niche, func.count(Analyse.id).label("nb_analyses"))
        .outerjoin(Analyse)
        .group_by(Niche.id)
        .order_by(Niche.created_at.desc())
        .limit(10)
    )
    result = await db.execute(stmt)
    niches_recentes = [
        {"niche": row[0], "nb_analyses": row[1]}
        for row in result.all()
    ]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "niches_recentes": niches_recentes,
    })


@router.post("/analyser", response_class=HTMLResponse)
async def analyser_niche(request: Request, db: AsyncSession = Depends(get_db)):
    """Lance une analyse pour un mot-clé donné."""
    form = await request.form()
    mot_cle = form.get("mot_cle", "").strip().lower()

    if not mot_cle or len(mot_cle) < 2:
        return templates.TemplateResponse("partials/erreur.html", {
            "request": request,
            "message": "Le mot-clé doit contenir au moins 2 caractères.",
        })

    # Vérifier si la niche existe déjà
    stmt = select(Niche).where(Niche.mot_cle == mot_cle)
    result = await db.execute(stmt)
    niche = result.scalar_one_or_none()

    if not niche:
        niche = Niche(mot_cle=mot_cle)
        db.add(niche)
        await db.flush()

    # Collecter les signaux (Phase 1 : Google Trends uniquement)
    trends_result = await fetch_google_trends(mot_cle)

    # Créer l'analyse
    signaux_data = [
        {
            "source": "google_trends",
            "donnees": trends_result["donnees"],
            "score_partiel": trends_result["score_partiel"],
        }
    ]

    scores = calculer_score_global(signaux_data)

    analyse = Analyse(
        niche_id=niche.id,
        score_global=scores["score_global"],
        score_demande=scores["score_demande"],
        score_douleur=scores["score_douleur"],
        score_concurrence=scores["score_concurrence"],
        score_monetisation=scores["score_monetisation"],
        opportunite=scores["opportunite"],
        verdict=scores["verdict"],
    )
    db.add(analyse)
    await db.flush()

    # Sauvegarder les signaux
    for s in signaux_data:
        signal = Signal(
            analyse_id=analyse.id,
            niche_id=niche.id,
            source=s["source"],
            donnees=s["donnees"],
            score_partiel=s["score_partiel"],
        )
        db.add(signal)

    await db.commit()

    # Recharger l'analyse avec ses signaux
    stmt = (
        select(Analyse)
        .where(Analyse.id == analyse.id)
        .options(selectinload(Analyse.signaux))
    )
    result = await db.execute(stmt)
    analyse = result.scalar_one()

    # Compter les analyses précédentes
    stmt_count = (
        select(func.count(Analyse.id))
        .where(Analyse.niche_id == niche.id)
    )
    result_count = await db.execute(stmt_count)
    nb_analyses = result_count.scalar()

    return templates.TemplateResponse("partials/fiche.html", {
        "request": request,
        "niche": niche,
        "analyse": analyse,
        "nb_analyses": nb_analyses,
    })


@router.get("/niche/{niche_id}", response_class=HTMLResponse)
async def page_niche(request: Request, niche_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Page détaillée d'une niche avec son historique d'analyses."""
    stmt = select(Niche).where(Niche.id == niche_id)
    result = await db.execute(stmt)
    niche = result.scalar_one_or_none()

    if not niche:
        return templates.TemplateResponse("partials/erreur.html", {
            "request": request,
            "message": "Niche introuvable.",
        }, status_code=404)

    # Récupérer toutes les analyses avec signaux
    stmt_analyses = (
        select(Analyse)
        .where(Analyse.niche_id == niche_id)
        .options(selectinload(Analyse.signaux))
        .order_by(Analyse.created_at.desc())
    )
    result_analyses = await db.execute(stmt_analyses)
    analyses = result_analyses.scalars().all()

    return templates.TemplateResponse("historique.html", {
        "request": request,
        "niche": niche,
        "analyses": analyses,
    })
