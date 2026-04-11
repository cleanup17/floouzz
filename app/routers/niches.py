"""Routes pour l'analyse et la consultation des niches (mode Analyse via pipeline_ia + serp_gap)."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Analyse, Niche, Signal, Thematique
from app.services.pipeline_ia import analyser as analyser_pipeline
from app.services.serp_gap import analyser_serp
from app.services.sources.google_trends import fetch_google_trends

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _construire_synthese(mot_cle: str, signaux: list[dict]) -> str:
    """
    Produit la synthese FR injectee dans pipeline_ia.analyser(contenu=...).

    Format fixe :
        "{mot_cle} — Tendance : {valeur}%. Interet : {description}.
         Sources : {nb_sources} signaux collectes."
    """
    # Extraction des donnees Google Trends (premier signal de ce type)
    variation_pct: float = 0.0
    description: str = "inconnu"

    for s in signaux:
        if s.get("source") == "google_trends":
            donnees = s.get("donnees") or {}
            variation_pct = float(donnees.get("variation_pct", 0) or 0)
            tendance = donnees.get("tendance", "inconnu")
            moyenne = donnees.get("moyenne", 0)
            description = f"{tendance}, moyenne {moyenne}/100"
            break

    nb_sources = len(signaux)
    return (
        f"{mot_cle} — Tendance : {variation_pct}%. "
        f"Interet : {description}. "
        f"Sources : {nb_sources} signaux collectes."
    )


async def _charger_thematiques_actives(db: AsyncSession) -> list[str]:
    """Charge les noms des thematiques actives pour alimenter le pipeline_ia."""
    stmt = select(Thematique.nom).where(Thematique.actif.is_(True))
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def redirect_accueil():
    """Redirige l'accueil vers le mode Decouverte."""
    return RedirectResponse(url="/decouverte/", status_code=302)


@router.get("/analyser", response_class=HTMLResponse)
async def page_analyser(
    request: Request,
    mot_cle: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Page d'analyse avec formulaire de recherche et liste des niches recentes."""
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

    return templates.TemplateResponse(request, "index.html", {
        "niches_recentes": niches_recentes,
        "mot_cle_prefill": mot_cle or "",
    })


@router.post("/analyser", response_class=HTMLResponse)
async def analyser_niche(request: Request, db: AsyncSession = Depends(get_db)):
    """Lance une analyse pour un mot-cle donne via pipeline_ia."""
    form = await request.form()
    mot_cle = form.get("mot_cle", "").strip().lower()

    if not mot_cle or len(mot_cle) < 2:
        return templates.TemplateResponse(request, "partials/erreur.html", {
            "message": "Le mot-cle doit contenir au moins 2 caracteres.",
        })

    # Recuperer ou creer la niche
    stmt = select(Niche).where(Niche.mot_cle == mot_cle)
    result = await db.execute(stmt)
    niche = result.scalar_one_or_none()
    if niche is None:
        niche = Niche(mot_cle=mot_cle)
        db.add(niche)
        await db.flush()

    # Collecte des signaux bruts (Google Trends pour l'instant)
    trends_result = await fetch_google_trends(mot_cle)
    signaux_data = [
        {
            "source": "google_trends",
            "donnees": trends_result["donnees"],
            "score_partiel": trends_result["score_partiel"],
        }
    ]

    # Construction de la synthese injectee dans pipeline_ia
    synthese = _construire_synthese(mot_cle, signaux_data)
    thematiques = await _charger_thematiques_actives(db)

    # Appels paralleles : pipeline_ia (enrichissement) + serp_gap (analyse SERP)
    # Les 2 services sont independants et beneficient chacun de leur cache
    # respectif dans cache_ia (source='analyse' vs source='serp_gap').
    resultat, gap = await asyncio.gather(
        analyser_pipeline(
            titre=mot_cle,
            contenu=synthese,
            donnees={"signaux": signaux_data},
            thematiques=thematiques,
            session=db,
            source="analyse",
        ),
        analyser_serp(mot_cle=mot_cle, session=db),
    )

    # Extraction des scores 0-10 avec leurs justifications
    scores = resultat["scores"]

    analyse = Analyse(
        niche_id=niche.id,
        score_global=resultat["score_global"],
        score_demande=scores["demande"]["valeur"],
        score_douleur=scores["douleur"]["valeur"],
        score_concurrence=scores["concurrence"]["valeur"],
        score_monetisation=scores["monetisation"]["valeur"],
        verdict=resultat["verdict"],
        verdict_raison=resultat["verdict_raison"],
        resume_fr=resultat["resume_fr"],
        mots_cles_seo=resultat["mots_cles_seo"],
        tags=resultat["tags"],
        risque_ymyl=resultat["risque_ymyl"],
        niche_detectee=resultat["niche_detectee"],
        pipeline_ia=resultat,
        serp_gap=gap,
    )
    db.add(analyse)
    await db.flush()

    # Persister les signaux bruts rattaches a l'analyse
    for s in signaux_data:
        db.add(Signal(
            analyse_id=analyse.id,
            niche_id=niche.id,
            source=s["source"],
            donnees=s["donnees"],
            score_partiel=s["score_partiel"],
        ))

    await db.commit()

    # Recharger l'analyse avec ses signaux pour le rendu
    stmt = (
        select(Analyse)
        .where(Analyse.id == analyse.id)
        .options(selectinload(Analyse.signaux))
    )
    result = await db.execute(stmt)
    analyse = result.scalar_one()

    stmt_count = select(func.count(Analyse.id)).where(Analyse.niche_id == niche.id)
    result_count = await db.execute(stmt_count)
    nb_analyses = result_count.scalar()

    return templates.TemplateResponse(request, "partials/fiche.html", {
        "niche": niche,
        "analyse": analyse,
        "nb_analyses": nb_analyses,
    })


@router.get("/niche/{niche_id}", response_class=HTMLResponse)
async def page_niche(
    request: Request,
    niche_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Page detaillee d'une niche avec son historique d'analyses."""
    stmt = select(Niche).where(Niche.id == niche_id)
    result = await db.execute(stmt)
    niche = result.scalar_one_or_none()

    if niche is None:
        return templates.TemplateResponse(
            request,
            "partials/erreur.html",
            {"message": "Niche introuvable."},
            status_code=404,
        )

    stmt_analyses = (
        select(Analyse)
        .where(Analyse.niche_id == niche_id)
        .options(selectinload(Analyse.signaux))
        .order_by(Analyse.created_at.desc())
    )
    result_analyses = await db.execute(stmt_analyses)
    analyses = result_analyses.scalars().all()

    return templates.TemplateResponse(request, "historique.html", {
        "niche": niche,
        "analyses": analyses,
    })
