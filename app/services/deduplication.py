"""
Service de deduplication des decouvertes.

Avant d'inserer une nouvelle decouverte, ce service verifie si un signal
proche n'existe pas deja dans les 7 derniers jours. Si c'est le cas, le
scanner reutilise le signal existant au lieu d'en creer un nouveau.

Criteres par ordre de priorite (chacun suffit a declarer un doublon) :
    1. niche_detectee identique (match exact, case-insensitive)
    2. au moins 2 tags en commun
    3. similarite de titre > 0.80 (difflib.SequenceMatcher)

Usage cote scanner :
    dedup_id = await chercher_doublon(session, titre, tags, niche_detectee)
    if dedup_id:
        # enrichir ou ignorer — ne pas inserer
    else:
        # inserer normalement
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Fenetre de recherche des doublons
FENETRE_JOURS = 7

# Seuils de detection
SEUIL_SIMILARITE_TITRE = 0.80
NB_TAGS_COMMUNS_MIN = 2


def _normaliser(texte: str | None) -> str:
    """Normalise une chaine pour comparaison (lowercase + strip)."""
    return (texte or "").strip().lower()


def _similarite_titre(a: str, b: str) -> float:
    """Ratio de similarite entre deux titres via SequenceMatcher."""
    return SequenceMatcher(None, _normaliser(a), _normaliser(b)).ratio()


async def chercher_doublon(
    session: AsyncSession,
    titre: str,
    tags: list[str],
    niche_detectee: str | None,
) -> uuid.UUID | None:
    """
    Cherche une decouverte doublon dans les 7 derniers jours.

    Args:
        session: session SQLAlchemy async
        titre: titre de la nouvelle decouverte (FR, deja traduit)
        tags: tags detectes par le pipeline IA (1-3)
        niche_detectee: niche detectee par le pipeline IA (peut etre None)

    Returns:
        UUID du signal existant si doublon detecte, sinon None.
        Le scanner doit alors enrichir l'existant plutot que d'inserer.
    """
    date_limite = datetime.now(timezone.utc) - timedelta(days=FENETRE_JOURS)

    # --- Critere 1 : match exact sur niche_detectee (SQL) --------------------
    # On pousse le filtre au plus pres de la BDD pour eviter de ramener
    # des centaines de lignes inutiles.
    if niche_detectee:
        try:
            result = await session.execute(
                text(
                    "SELECT id FROM decouvertes "
                    "WHERE niche_detectee IS NOT NULL "
                    "  AND LOWER(niche_detectee) = LOWER(:n) "
                    "  AND created_at >= :d "
                    "ORDER BY created_at DESC "
                    "LIMIT 1"
                ),
                {"n": niche_detectee, "d": date_limite},
            )
            row = result.first()
            if row:
                logger.info(
                    f"Dedup : doublon via niche_detectee='{niche_detectee}' "
                    f"-> {row[0]}"
                )
                return row[0]
        except Exception as e:
            # Colonne absente ou erreur : on continue sur les autres criteres
            logger.debug(f"Dedup : requete niche_detectee echouee ({e})")

    # --- Criteres 2 et 3 : on charge les candidats des 7 derniers jours ------
    # On pre-filtre en SQL : au moins un tag en commun OU un debut de titre
    # proche. Ca limite fortement le volume a traiter en Python.
    candidats = []
    try:
        if tags:
            # Operateur && = "au moins un element en commun" sur ARRAY postgres
            result = await session.execute(
                text(
                    "SELECT id, titre, tags "
                    "FROM decouvertes "
                    "WHERE created_at >= :d "
                    "  AND tags && CAST(:t AS VARCHAR[]) "
                    "ORDER BY created_at DESC "
                    "LIMIT 100"
                ),
                {"d": date_limite, "t": tags},
            )
            candidats = list(result.fetchall())

        # Si pas assez de candidats via tags, on elargit sur les titres proches
        # (meme initiale + longueur comparable) pour le critere similarite.
        if len(candidats) < 50 and titre:
            prefixe = _normaliser(titre)[:8]
            if prefixe:
                result = await session.execute(
                    text(
                        "SELECT id, titre, tags "
                        "FROM decouvertes "
                        "WHERE created_at >= :d "
                        "  AND LOWER(titre) LIKE :p "
                        "ORDER BY created_at DESC "
                        "LIMIT 100"
                    ),
                    {"d": date_limite, "p": f"{prefixe}%"},
                )
                candidats.extend(list(result.fetchall()))
    except Exception as e:
        logger.debug(f"Dedup : requete candidats echouee ({e})")
        return None

    if not candidats:
        return None

    # --- Critere 2 : au moins 2 tags en commun -------------------------------
    tags_set = {_normaliser(t) for t in tags if t}
    vus: set[uuid.UUID] = set()
    for row in candidats:
        cand_id, cand_titre, cand_tags = row[0], row[1], row[2] or []
        if cand_id in vus:
            continue
        vus.add(cand_id)

        cand_tags_set = {_normaliser(t) for t in cand_tags if t}
        if len(tags_set & cand_tags_set) >= NB_TAGS_COMMUNS_MIN:
            logger.info(
                f"Dedup : doublon via {NB_TAGS_COMMUNS_MIN}+ tags communs "
                f"-> {cand_id}"
            )
            return cand_id

    # --- Critere 3 : similarite de titre > 0.80 ------------------------------
    for row in candidats:
        cand_id, cand_titre = row[0], row[1]
        ratio = _similarite_titre(titre, cand_titre)
        if ratio > SEUIL_SIMILARITE_TITRE:
            logger.info(
                f"Dedup : doublon via similarite titre ({ratio:.2f}) "
                f"-> {cand_id}"
            )
            return cand_id

    return None
