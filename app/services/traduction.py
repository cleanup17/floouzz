"""
Service de traduction EN -> FR via Claude API.

Role unique : pre-traitement des contenus de sources anglophones
(Reddit, Hacker News, Product Hunt...) AVANT leur passage dans pipeline_ia.

Flux attendu :
    source anglophone -> traduire(titre, contenu) -> pipeline_ia.analyser(...)

Le pipeline IA recoit toujours du francais en entree. Ce service n'est
jamais appele par pipeline_ia lui-meme.
"""

import json
import logging
import re

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Modele leger pour la traduction : un appel rapide, pas d'analyse
MODELE_TRADUCTION = "claude-haiku-4-5-20251001"

# Longueur maximale du contenu envoye a Claude (pour maitriser le cout)
MAX_CONTENU_CHARS = 3000


def _besoin_traduction(titre: str, contenu: str) -> bool:
    """
    Heuristique rapide pour eviter les appels Claude inutiles.

    Si le texte ne contient quasiment que de l'ASCII sans mots francais
    evidents, on considere qu'il est en anglais et on traduit.
    Dans le doute, on traduit — le pipeline IA attend du FR.
    """
    texte = f"{titre} {contenu}".lower()
    # Mots francais tres frequents : si on en voit plusieurs, c'est deja du FR
    marqueurs_fr = (" le ", " la ", " les ", " des ", " une ", " un ",
                    " pour ", " avec ", " dans ", " est ", " qui ", " que ")
    return sum(1 for m in marqueurs_fr if m in f" {texte} ") < 2


async def traduire(titre: str, contenu: str) -> tuple[str, str]:
    """
    Traduit un couple (titre, contenu) de l'anglais vers le francais.

    Un seul appel Claude avec un prompt minimaliste qui retourne un JSON
    a deux cles pour garantir l'alignement titre/contenu.

    Args:
        titre: titre source (anglais ou deja francais)
        contenu: contenu source (anglais ou deja francais)

    Returns:
        (titre_fr, contenu_fr) — en cas d'echec ou si la cle API manque,
        retourne le couple d'origine sans traduction.
    """
    # Pas de cle API : on retourne tel quel
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — traduction ignoree")
        return titre, contenu

    # Heuristique : probablement deja en francais, on ne fait rien
    if not _besoin_traduction(titre, contenu):
        return titre, contenu

    # Troncature pour maitriser les tokens
    contenu_source = contenu[:MAX_CONTENU_CHARS]

    prompt = f"""Traduis ce titre et ce contenu de l'anglais vers le francais.
Reponds UNIQUEMENT avec un objet JSON au format suivant, rien d'autre :

{{"titre": "<traduction du titre>", "contenu": "<traduction du contenu>"}}

Titre : {titre}
Contenu : {contenu_source}"""

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_TRADUCTION,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
    except Exception as e:
        logger.error(f"Traduction : erreur appel Claude — {e}")
        return titre, contenu

    # Parsing robuste : extraction du premier {...} si Claude ajoute du texte
    data = None
    try:
        data = json.loads(reponse)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", reponse, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if not isinstance(data, dict):
        logger.warning(f"Traduction : JSON illisible, fallback ({reponse[:150]})")
        return titre, contenu

    titre_fr = str(data.get("titre") or titre)
    contenu_fr = str(data.get("contenu") or contenu)
    logger.info(f"Traduction EN->FR : '{titre[:40]}' -> '{titre_fr[:40]}'")
    return titre_fr, contenu_fr


# ---------------------------------------------------------------------------
# Traduction batch de titres — utilisee par les connecteurs sources anglophones
# ---------------------------------------------------------------------------

# Nombre max de titres traites en un seul appel Claude pour maitriser le cout
MAX_TITRES_PAR_BATCH = 20


async def traduire_titres(titres: list[str]) -> list[str]:
    """
    Traduit une liste de titres de l'anglais vers le francais en un seul appel Claude.

    Utilise par les connecteurs sources anglophones (reddit, hackernews,
    producthunt) pour pre-traduire leurs titres avant le passage dans le
    scanner / pipeline_ia. Fait UN appel Claude par batch au lieu d'un par titre.

    Args:
        titres: liste de titres sources (anglais, deja dedoublonnes par l'appelant)

    Returns:
        liste de titres FR dans le meme ordre. Sur erreur ou absence de cle API,
        retourne la liste d'origine inchangee.
    """
    if not titres:
        return []

    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — titres non traduits")
        return titres

    # Filtrage : on ne traduit que les titres qui ont besoin de l'etre
    a_traduire_idx: list[int] = []
    for i, t in enumerate(titres):
        if _besoin_traduction(t, ""):
            a_traduire_idx.append(i)

    if not a_traduire_idx:
        return titres

    # Decoupage en batchs pour rester sous la limite de tokens
    resultat = list(titres)
    for debut in range(0, len(a_traduire_idx), MAX_TITRES_PAR_BATCH):
        lot_idx = a_traduire_idx[debut:debut + MAX_TITRES_PAR_BATCH]
        lot_titres = [titres[i] for i in lot_idx]
        traduits = await _traduire_lot(lot_titres)
        for i, t in zip(lot_idx, traduits):
            resultat[i] = t

    return resultat


async def _traduire_lot(titres: list[str]) -> list[str]:
    """Traduit un lot de titres (<=MAX_TITRES_PAR_BATCH) en un seul appel Claude."""
    # Numerotation [0], [1]... pour que Claude preserve l'ordre meme si un titre
    # contient un retour a la ligne ou un caractere special
    lignes = "\n".join(f"[{i}] {t}" for i, t in enumerate(titres))
    prompt = f"""Traduis ces titres de l'anglais vers le francais.
Reponds UNIQUEMENT avec un tableau JSON d'objets au format suivant, rien d'autre :

[{{"index": 0, "titre": "<traduction>"}}, {{"index": 1, "titre": "<traduction>"}}, ...]

TITRES :
{lignes}"""

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_TRADUCTION,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
    except Exception as e:
        logger.error(f"Traduction batch : erreur appel Claude — {e}")
        return titres

    # Parsing robuste : extraction du premier tableau JSON
    data = None
    try:
        data = json.loads(reponse)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", reponse, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if not isinstance(data, list):
        logger.warning(f"Traduction batch : JSON illisible ({reponse[:150]})")
        return titres

    # Reconstruction dans l'ordre via les index retournes par Claude
    traduits = list(titres)
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        titre_fr = item.get("titre")
        if isinstance(idx, int) and 0 <= idx < len(traduits) and titre_fr:
            traduits[idx] = str(titre_fr)

    logger.info(f"Traduction batch : {len(titres)} titres EN->FR")
    return traduits
