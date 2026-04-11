"""Routes du mode Decouverte — dashboard de signaux quotidiens (format pipeline_ia)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
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
    verdict: str | None = Query(None, pattern="^(GO|WATCH|SKIP)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Dashboard des signaux du jour.

    Par defaut on masque :
    - les signaux au statut 'ignore' (dedup ou prefs utilisatrice)
    - les verdicts SKIP (juges non pertinents par pipeline_ia)
    Les signaux sans verdict (pas encore enrichis) restent affiches en fin de liste.
    """
    stmt = (
        select(Decouverte)
        .where(Decouverte.scan_date == date.today())
        .where(Decouverte.statut != "ignore")
        .order_by(Decouverte.score_global.desc().nulls_last())
    )

    # Filtre verdict : soit par defaut on cache les SKIP, soit on affiche
    # uniquement un verdict precis demande via query param
    if verdict:
        stmt = stmt.where(Decouverte.verdict == verdict)
    else:
        # Masquer les SKIP par defaut — on garde GO, WATCH et non-enrichis (NULL)
        stmt = stmt.where((Decouverte.verdict != "SKIP") | (Decouverte.verdict.is_(None)))

    if tag:
        stmt = stmt.where(Decouverte.tags.any(tag))

    result = await db.execute(stmt)
    signaux = list(result.scalars().all())

    # Compter les signaux par tag (pour les pills de filtre)
    tags_count: dict[str, int] = {}
    for s in signaux:
        for t in (s.tags or []):
            tags_count[t] = tags_count.get(t, 0) + 1

    # Compter par verdict (pour afficher le nombre de GO/WATCH dans la UI)
    verdict_count = {
        "GO": sum(1 for s in signaux if s.verdict == "GO"),
        "WATCH": sum(1 for s in signaux if s.verdict == "WATCH"),
    }

    # Thematiques configurees
    stmt_themes = (
        select(Thematique)
        .where(Thematique.actif.is_(True))
        .order_by(Thematique.nom)
    )
    result_themes = await db.execute(stmt_themes)
    thematiques = result_themes.scalars().all()

    # Stats globales du jour (tous statuts confondus)
    stmt_total = (
        select(func.count(Decouverte.id))
        .where(Decouverte.scan_date == date.today())
    )
    result_total = await db.execute(stmt_total)
    total_signaux = result_total.scalar() or 0

    return templates.TemplateResponse(request, "decouverte.html", {
        "signaux": signaux,
        "tags_count": tags_count,
        "verdict_count": verdict_count,
        "thematiques": thematiques,
        "tag_actif": tag,
        "verdict_actif": verdict,
        "total_signaux": total_signaux,
        "nb_affiches": len(signaux),
        "date_scan": date.today(),
    })


@router.post("/rafraichir", response_class=HTMLResponse)
async def rafraichir(request: Request, db: AsyncSession = Depends(get_db)):
    """Relance un scan complet (collecte + enrichissement)."""
    await run_scan_complet()
    return await page_decouverte(request, tag=None, verdict=None, db=db)


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
    """
    Approfondir un signal — redirige vers l'analyse avec le mot-cle.
    Priorite au mot-cle : niche_detectee (pipeline_ia) > mot_cle_suggere (legacy/webhook)
    > debut du titre.
    """
    stmt = select(Decouverte).where(Decouverte.id == decouverte_id)
    result = await db.execute(stmt)
    decouverte = result.scalar_one_or_none()

    if not decouverte:
        return templates.TemplateResponse(request, "partials/erreur.html", {
            "message": "Signal introuvable.",
        })

    mot_cle = (
        decouverte.niche_detectee
        or decouverte.mot_cle_suggere
        or decouverte.titre[:100]
    )
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
