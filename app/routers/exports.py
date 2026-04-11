"""Routes d'export — fiches niches en Markdown (format pipeline_ia natif)."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Analyse, Niche

router = APIRouter(prefix="/exports", tags=["exports"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(texte: str) -> str:
    """Produit un slug simple pour le nom de fichier."""
    clean = "".join(c if c.isalnum() or c in "-_ " else "" for c in texte)
    return clean.strip().replace(" ", "_")[:80] or "niche"


def _justification(analyse: Analyse, dimension: str) -> str:
    """Lit la justification d'une dimension depuis le JSONB pipeline_ia."""
    if not analyse.pipeline_ia:
        return ""
    scores = analyse.pipeline_ia.get("scores", {})
    return scores.get(dimension, {}).get("justification", "")


# ---------------------------------------------------------------------------
# Generation du Markdown
# ---------------------------------------------------------------------------

def _rendre_markdown(niche: Niche, analyse: Analyse) -> str:
    """Produit le Markdown de la fiche niche a partir des champs pipeline_ia natifs."""
    # Titre : niche detectee par l'IA si presente, sinon mot-cle saisi
    titre = analyse.niche_detectee or niche.mot_cle
    date_str = analyse.created_at.strftime("%Y-%m-%d %H:%M")

    lignes = [
        f"# {titre}",
        "",
        f"**Score global** : {analyse.score_global}/10 — verdict {analyse.verdict}",
        f"**Date** : {date_str}",
        "",
        "## Resume",
        "",
        analyse.resume_fr or "",
        "",
        "## Scores",
        "",
        f"- Demande : {analyse.score_demande}/10 — {_justification(analyse, 'demande')}",
        f"- Douleur : {analyse.score_douleur}/10 — {_justification(analyse, 'douleur')}",
        f"- Concurrence : {analyse.score_concurrence}/10 — {_justification(analyse, 'concurrence')}",
        f"- Monetisation : {analyse.score_monetisation}/10 — {_justification(analyse, 'monetisation')}",
        "",
    ]

    if analyse.verdict_raison:
        lignes += ["## Verdict", "", analyse.verdict_raison, ""]

    lignes += ["## Mots-cles SEO", ""]
    if analyse.mots_cles_seo:
        lignes += [f"- {m}" for m in analyse.mots_cles_seo]
    else:
        lignes.append("_Aucun mot-cle SEO._")
    lignes.append("")

    lignes += ["## Tags", ""]
    if analyse.tags:
        lignes += [f"- {t}" for t in analyse.tags]
    else:
        lignes.append("_Aucun tag._")
    lignes.append("")

    lignes.append(f"⚠️ YMYL : {'Oui' if analyse.risque_ymyl else 'Non'}")
    lignes.append("")

    # --- Concurrence SEO (SERP Gap Detector) ---------------------------------
    # Section ajoutee si l'analyse a un snapshot serp_gap (analyses post-v0.5).
    # Les anciennes analyses sans ce champ sautent ce bloc proprement.
    if analyse.serp_gap:
        lignes += _rendre_serp_gap_markdown(analyse.serp_gap)

    return "\n".join(lignes)


def _rendre_serp_gap_markdown(gap: dict) -> list[str]:
    """
    Rend la section Concurrence SEO en Markdown a partir d'un dict serp_gap.
    Retourne une liste de lignes prete a etre concatenee au reste du document.
    """
    lignes: list[str] = [
        "## Concurrence SEO",
        "",
        f"**Score difficulte** : {gap.get('score_difficulte', 'N/A')}/10 — {gap.get('verdict', 'N/A')}",
        "",
    ]

    raison = gap.get("verdict_raison")
    if raison:
        lignes += [raison, ""]

    # Opportunites
    opportunites = gap.get("opportunites") or []
    lignes += ["### Opportunites", ""]
    if opportunites:
        lignes += [f"- {o}" for o in opportunites]
    else:
        lignes.append("_Aucune opportunite identifiee._")
    lignes.append("")

    # Faiblesses detectees
    faiblesses = gap.get("faiblesses_detectees") or []
    lignes += ["### Faiblesses detectees", ""]
    if faiblesses:
        lignes += [f"- {f}" for f in faiblesses]
    else:
        lignes.append("_Aucune faiblesse detectee._")
    lignes.append("")

    # Top 10 Google (tableau Markdown)
    top_10 = gap.get("top_10") or []
    if top_10:
        lignes += [
            "### Top 10 Google",
            "",
            "| # | Titre | Domaine | Type |",
            "| --- | --- | --- | --- |",
        ]
        for item in top_10:
            titre = (item.get("titre") or "").replace("|", "\\|")[:80]
            domaine = item.get("domaine") or ""
            type_page = item.get("type_page") or ""
            position = item.get("position", "")
            lignes.append(f"| {position} | {titre} | {domaine} | {type_page} |")
        lignes.append("")

    return lignes


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/niche/{niche_id}/markdown")
async def exporter_niche_markdown(
    niche_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Exporte la fiche de la derniere analyse d'une niche en Markdown telechargeable."""
    stmt = (
        select(Niche)
        .where(Niche.id == niche_id)
        .options(selectinload(Niche.analyses))
    )
    result = await db.execute(stmt)
    niche = result.scalar_one_or_none()

    if niche is None:
        raise HTTPException(status_code=404, detail="Niche introuvable")

    if not niche.analyses:
        raise HTTPException(
            status_code=404,
            detail="Aucune analyse disponible pour cette niche",
        )

    # Relation triee DESC par created_at dans le modele
    derniere_analyse = niche.analyses[0]
    markdown = _rendre_markdown(niche, derniere_analyse)

    date_fichier = derniere_analyse.created_at.strftime("%Y%m%d")
    nom_fichier = f"floouzz_{_slug(niche.mot_cle)}_{date_fichier}.md"
    filename_encoded = quote(nom_fichier)

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{nom_fichier}\"; "
                f"filename*=UTF-8''{filename_encoded}"
            ),
        },
    )
