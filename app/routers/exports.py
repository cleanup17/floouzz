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

    # --- Affiliation (Affiliate Finder) --------------------------------------
    # Section ajoutee si l'analyse a un snapshot affiliate_finder (v0.5.2+).
    if analyse.affiliate_finder:
        lignes += _rendre_affiliate_markdown(analyse.affiliate_finder)

    # --- Saisonnalite --------------------------------------------------------
    # Section ajoutee si l'analyse a un snapshot saisonnalite (v0.5.5+).
    if analyse.saisonnalite:
        lignes += _rendre_saisonnalite_markdown(analyse.saisonnalite)

    # --- Marketplace Gap -----------------------------------------------------
    # Section ajoutee si l'analyse a un snapshot marketplace_gap (v0.5.6+).
    if analyse.marketplace_gap:
        lignes += _rendre_marketplace_markdown(analyse.marketplace_gap)

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


def _rendre_affiliate_markdown(aff: dict) -> list[str]:
    """
    Rend la section Affiliation en Markdown a partir d'un dict affiliate_finder.
    Retourne une liste de lignes prete a etre concatenee au reste du document.
    """
    # Titre avec compteur de plateformes (comme dans l'UI HTML)
    plateformes = aff.get("plateformes_detectees") or []
    if plateformes:
        titre_section = f"## Affiliation ({len(plateformes)} plateforme"
        if len(plateformes) > 1:
            titre_section += "s"
        titre_section += ")"
    else:
        titre_section = "## Affiliation"

    lignes: list[str] = [
        titre_section,
        "",
        f"**Score affiliation** : {aff.get('score_affiliation', 'N/A')}/10 — {aff.get('verdict', 'N/A')}",
        "",
    ]

    raison = aff.get("verdict_raison")
    if raison:
        lignes += [raison, ""]

    # Plateformes detectees
    lignes += ["### Plateformes detectees", ""]
    if plateformes:
        lignes += [f"- {p}" for p in plateformes]
    else:
        lignes.append("_Aucune plateforme detectee._")
    lignes.append("")

    # Programmes identifies (tableau Markdown)
    programmes = aff.get("programmes") or []
    if programmes:
        lignes += [
            "### Programmes identifies",
            "",
            "| Nom | Plateforme | Commission | Cookie |",
            "| --- | --- | --- | --- |",
        ]
        for prog in programmes:
            nom = (prog.get("nom") or "").replace("|", "\\|")[:80]
            plateforme = (prog.get("plateforme") or "").replace("|", "\\|")[:50]
            commission = prog.get("commission") or "-"
            cookie = prog.get("cookie_duree") or "-"
            lignes.append(f"| {nom} | {plateforme} | {commission} | {cookie} |")
        lignes.append("")

    # Opportunites
    opportunites = aff.get("opportunites") or []
    lignes += ["### Opportunites", ""]
    if opportunites:
        lignes += [f"- {o}" for o in opportunites]
    else:
        lignes.append("_Aucune opportunite identifiee._")
    lignes.append("")

    # Requetes Google utilisees (tracabilite)
    requetes = aff.get("requetes_utilisees") or []
    if requetes:
        lignes += ["### Requetes Google utilisees", ""]
        lignes += [f"- `{r}`" for r in requetes]
        lignes.append("")

    return lignes


def _rendre_saisonnalite_markdown(sais: dict) -> list[str]:
    """
    Rend la section Saisonnalite en Markdown a partir d'un dict saisonnalite.
    Retourne une liste de lignes prete a etre concatenee au reste du document.
    Ne retourne PAS la serie complete (53 points = trop verbeux pour Markdown).
    """
    lignes: list[str] = [
        "## Saisonnalite",
        "",
        f"**Score** : {sais.get('score_saisonnalite', 'N/A')}/10 — {sais.get('verdict', 'N/A')}",
        "",
    ]

    raison = sais.get("verdict_raison")
    if raison:
        lignes += [raison, ""]

    # Pic principal
    pic = sais.get("pic") or {}
    if pic.get("mois_principal") or pic.get("date_pic"):
        lignes += ["### Pic principal", ""]
        if pic.get("mois_principal"):
            mois = str(pic["mois_principal"]).capitalize()
            valeur = pic.get("valeur_pic")
            if valeur:
                lignes.append(f"- **Mois** : {mois} ({valeur}/100)")
            else:
                lignes.append(f"- **Mois** : {mois}")
        if pic.get("date_pic"):
            lignes.append(f"- **Semaine du pic** : {pic['date_pic']}")
        if pic.get("semaines_top_80pct"):
            lignes.append(
                f"- **Concentration** : 80% du signal sur "
                f"{pic['semaines_top_80pct']} semaines"
            )
        lignes.append("")

    # Phase actuelle
    position = sais.get("position_actuelle") or {}
    if position.get("phase"):
        lignes += ["### Phase actuelle", ""]
        phase = str(position["phase"]).capitalize()
        lignes.append(f"- **Phase** : {phase}")
        if position.get("mois_actuel"):
            lignes.append(f"- **Mois courant** : {position['mois_actuel']}")
        distance = position.get("distance_au_pic_mois")
        if distance is not None and distance > 0:
            lignes.append(f"- **Distance au prochain pic** : {distance} mois")
        lignes.append("")

    # Recommandations
    recommandations = sais.get("recommandations") or []
    lignes += ["### Recommandations", ""]
    if recommandations:
        lignes += [f"- {r}" for r in recommandations]
    else:
        lignes.append("_Aucune recommandation generee._")
    lignes.append("")

    # Stats techniques (tableau Markdown compact)
    stats = sais.get("stats") or {}
    if stats and stats.get("nb_points"):
        concentration = stats.get("concentration_top8", 0)
        concentration_pct = int(concentration * 100) if concentration else 0
        lignes += [
            "### Stats techniques",
            "",
            "| Metrique | Valeur |",
            "| --- | --- |",
            f"| Nb points (semaines) | {stats.get('nb_points', 0)} |",
            f"| Min | {stats.get('min', 0)} |",
            f"| Max | {stats.get('max', 0)} |",
            f"| Moyenne | {stats.get('moyenne', 0)} |",
            f"| Mediane | {stats.get('mediane', 0)} |",
            f"| Ecart-type | {stats.get('ecart_type', 0)} |",
            f"| Ratio pic/moyenne | {stats.get('ratio_pic_moyenne', 0)} |",
            f"| Coefficient variation | {stats.get('coefficient_variation', 0)} |",
            f"| Concentration top 8 | {concentration_pct}% |",
            "",
        ]

    return lignes


def _rendre_marketplace_markdown(mp: dict) -> list[str]:
    """
    Rend la section Marketplace Gap en Markdown.
    Retourne une liste de lignes prete a etre concatenee au document.
    """
    nb_actives = mp.get("plateformes_actives", 0)
    details = mp.get("details_par_plateforme") or []
    nb_total = len(details) if details else 3

    if nb_actives:
        titre = f"## Marketplaces ({nb_actives}/{nb_total} actives)"
    else:
        titre = "## Marketplaces"

    lignes: list[str] = [
        titre,
        "",
        f"**Score** : {mp.get('score_marketplace', 'N/A')}/10 — {mp.get('verdict', 'N/A')}",
        "",
    ]

    raison = mp.get("verdict_raison")
    if raison:
        lignes += [raison, ""]

    # Detail par plateforme (tableau Markdown)
    if details:
        lignes += [
            "### Detail par plateforme",
            "",
            "| Plateforme | Resultats | Echantillon |",
            "| --- | --- | --- |",
        ]
        for plat in details:
            nom = plat.get("nom") or ""
            if plat.get("erreur"):
                lignes.append(f"| {nom} | erreur | - |")
            else:
                total = plat.get("total_resultats", 0)
                echantillon = plat.get("echantillon_visible", 0)
                lignes.append(f"| {nom} | {total} | {echantillon} |")
        lignes.append("")

    # Recommandations
    recommandations = mp.get("recommandations") or []
    lignes += ["### Recommandations", ""]
    if recommandations:
        lignes += [f"- {r}" for r in recommandations]
    else:
        lignes.append("_Aucune recommandation generee._")
    lignes.append("")

    # Requetes Google (tracabilite)
    requetes = mp.get("requetes_utilisees") or []
    if requetes:
        lignes += ["### Requetes Google utilisees", ""]
        lignes += [f"- `{r}`" for r in requetes]
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
