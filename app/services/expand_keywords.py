"""
Service expansion de mots-cles longue traine — via Claude Haiku 4.5.

Pour un mot-cle principal et un contexte de niche (resume_fr de pipeline_ia) :
1. Un seul appel Claude Haiku qui genere 15-20 variantes longue traine FR
2. Groupees par intention : informationnelle, commerciale, transactionnelle,
   locale (optionnel si pas de dimension geographique naturelle)
3. Retour JSON structure pret a l'affichage dans fiche.html

Pas de persistance BDD pour l'instant — le resultat est retourne en memoire
et affiche dans la fiche. Pas de cache non plus (chaque appel = 1 Claude
Haiku frais, ~0.002$).

Pas de SerpAPI — la generation de variantes est un travail linguistique pur
que Claude fait mieux que n'importe quel outil de scraping.
"""

import json
import logging
import re
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Haiku 4.5 suffit largement pour la generation de mots-cles (pas de jugement
# complexe, juste de la creativite linguistique). 5x moins cher que Sonnet.
MODELE_CLAUDE = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1500

# Intentions valides — l'ordre est preservee dans le retour
INTENTIONS_VALIDES = ("informationnelle", "commerciale", "transactionnelle", "locale")


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec."""
    return {
        "mot_cle": mot_cle,
        "nb_variantes": 0,
        "par_intention": {
            "informationnelle": [],
            "commerciale": [],
            "transactionnelle": [],
            "locale": [],
        },
        "erreur": raison,
    }


# ---------------------------------------------------------------------------
# Prompt Claude
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """Tu es un expert SEO francophone specialise dans la recherche de mots-cles
longue traine exploitables pour une entrepreneure independante.

MOT-CLE PRINCIPAL : {mot_cle}
{contexte_bloc}

Genere 15 a 20 variantes longue traine en francais, groupees par intention
de recherche. Chaque variante doit etre un mot-cle concret que quelqu'un
taperait dans Google, pas une phrase generique.

INTENTIONS A COUVRIR :

1. INFORMATIONNELLE (5-7 variantes) : comment, pourquoi, guide, tutoriel,
   difference, explication, danger, avis d'expert, c'est quoi.
   Mots-cles que tape quelqu'un qui CHERCHE A COMPRENDRE.

2. COMMERCIALE (4-6 variantes) : meilleur, comparatif, avis, top,
   alternative, vs, test, classement.
   Mots-cles que tape quelqu'un qui COMPARE avant d'acheter.

3. TRANSACTIONNELLE (3-5 variantes) : acheter, prix, livraison, pas cher,
   promo, coffret, abonnement, commande, ou trouver.
   Mots-cles que tape quelqu'un PRET A ACHETER.

4. LOCALE (0-3 variantes) : uniquement si la niche a une dimension
   geographique naturelle (produit physique, service local, magasin).
   Si la niche est purement numerique/en ligne, retourne une liste vide.

REGLES :
- Chaque variante doit contenir le mot-cle principal ou un synonyme proche
- Privilegier les formulations naturelles en francais (pas du keyword stuffing)
- Viser des mots-cles avec un volume potentiel realiste (pas trop nichés)
- Ne pas repeter le meme mot-cle avec juste un adjectif en plus

Ta reponse doit etre UN SEUL objet JSON, SANS texte autour :

{{
  "informationnelle": ["<variante 1>", "<variante 2>", ...],
  "commerciale": ["<variante 1>", ...],
  "transactionnelle": ["<variante 1>", ...],
  "locale": ["<variante 1>", ...] ou [] si pas pertinent
}}

Reponds UNIQUEMENT avec l'objet JSON. Pas de markdown, pas de commentaires."""


def _construire_prompt(mot_cle: str, contexte: str | None) -> str:
    """Construit le prompt avec le contexte optionnel de pipeline_ia."""
    contexte_bloc = ""
    if contexte:
        contexte_bloc = f"CONTEXTE DE LA NICHE : {contexte[:500]}"
    return PROMPT_TEMPLATE.format(
        mot_cle=mot_cle,
        contexte_bloc=contexte_bloc,
    )


# ---------------------------------------------------------------------------
# Parsing JSON robuste
# ---------------------------------------------------------------------------

def _extraire_json(texte: str) -> dict | None:
    """Parse direct -> regex {...} -> nettoyage fences markdown."""
    if not texte:
        return None
    try:
        return json.loads(texte.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", texte, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    nettoye = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.MULTILINE)
    try:
        return json.loads(nettoye)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Normalisation du retour Claude
# ---------------------------------------------------------------------------

def _normaliser_resultat(brut: dict, mot_cle: str) -> dict[str, Any]:
    """
    Valide et nettoie le JSON retourne par Claude.
    Garantit que les 4 intentions sont presentes avec des listes de strings.
    """
    par_intention: dict[str, list[str]] = {}

    for intention in INTENTIONS_VALIDES:
        raw = brut.get(intention, [])
        if not isinstance(raw, list):
            raw = []
        # Nettoie chaque variante : string non vide, tronquee a 150 chars
        variantes = [
            str(v).strip()[:150]
            for v in raw
            if isinstance(v, str) and v.strip()
        ]
        par_intention[intention] = variantes

    nb_total = sum(len(v) for v in par_intention.values())

    return {
        "mot_cle": mot_cle,
        "nb_variantes": nb_total,
        "par_intention": par_intention,
    }


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def expand_keywords(
    mot_cle: str,
    contexte: str | None = None,
) -> dict[str, Any]:
    """
    Genere 15-20 variantes longue traine d'un mot-cle, groupees par
    intention (informationnelle, commerciale, transactionnelle, locale).

    Args:
        mot_cle: mot-cle principal a expanser
        contexte: resume_fr de pipeline_ia pour affiner les variantes
            (optionnel, mais ameliore significativement la pertinence)

    Returns:
        dict avec les cles :
        - mot_cle : str
        - nb_variantes : int
        - par_intention : dict avec 4 listes (informationnelle, commerciale,
                         transactionnelle, locale)
        - erreur : str (absent si pas d'erreur)

    Tous les chemins d'erreur retournent un resultat vide (nb_variantes=0,
    listes vides) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    if not settings.ANTHROPIC_API_KEY:
        return _resultat_par_defaut(mot_cle, "cle_api_absente")

    prompt = _construire_prompt(mot_cle, contexte)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
        logger.info(
            f"Expand Keywords : reponse Haiku ({len(reponse)} chars) "
            f"pour '{mot_cle[:40]}'"
        )
    except Exception as e:
        logger.error(f"Expand Keywords : erreur Claude — {e}")
        return _resultat_par_defaut(mot_cle, f"erreur_api: {e}")

    brut = _extraire_json(reponse)
    if brut is None:
        logger.warning(
            f"Expand Keywords : JSON illisible ({reponse[:200]})"
        )
        return _resultat_par_defaut(mot_cle, "json_illisible")

    resultat = _normaliser_resultat(brut, mot_cle)
    logger.info(
        f"Expand Keywords : {resultat['nb_variantes']} variantes "
        f"pour '{mot_cle[:40]}'"
    )
    return resultat
