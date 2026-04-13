"""
Service Keyword Clustering — regroupement des variantes longue traine en
sous-niches testables via Claude Haiku 4.5.

Prend en entree les 15-20 variantes de expand_keywords() et les regroupe
en 5-7 clusters thematiques coherents. Chaque cluster represente un angle
d'attaque different pour exploiter un marche :
  - nom : label court (2-4 mots)
  - mots_cles : 3-5 variantes associees
  - score_potentiel : 0-10 (demande + facilite + monetisabilite)
  - monetisation : modele le plus naturel
  - raison : justification en 1 phrase

Appele sequentiellement apres expand_keywords() dans /analyser. Dependance
lineaire : pipeline_ia -> expand_keywords -> cluster_keywords.

Pas de persistance BDD pour l'instant — resultat retourne en memoire.
Pas de cache — chaque appel = 1 Claude Haiku frais (~$0.002).
"""

import json
import logging
import re
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

MODELE_CLAUDE = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1500


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec ou donnees insuffisantes."""
    return {
        "mot_cle": mot_cle,
        "nb_clusters": 0,
        "clusters": [],
        "erreur": raison,
    }


# ---------------------------------------------------------------------------
# Extraction des variantes depuis le dict expand_keywords
# ---------------------------------------------------------------------------

def _aplatir_variantes(keywords: dict[str, Any]) -> list[str]:
    """
    Extrait toutes les variantes de expand_keywords() en une liste plate.
    Supprime le groupement par intention — Claude va re-grouper autrement,
    par sous-niche thematique.
    """
    par_intention = keywords.get("par_intention") or {}
    variantes: list[str] = []
    for intention in ("informationnelle", "commerciale", "transactionnelle", "locale"):
        items = par_intention.get(intention) or []
        for v in items:
            if isinstance(v, str) and v.strip():
                variantes.append(v.strip())
    return variantes


# ---------------------------------------------------------------------------
# Prompt Claude
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """Tu es un expert en strategie de niche qui aide une entrepreneure
independante a identifier des sous-niches testables rapidement.

MOT-CLE PRINCIPAL : {mot_cle}
{contexte_bloc}

MOTS-CLES A REGROUPER ({nb_variantes}) :
{liste_variantes}

Ta mission : regrouper ces mots-cles en 5 a 7 sous-niches coherentes et
testables. Chaque sous-niche represente un angle d'attaque different pour
exploiter ce marche.

Pour chaque sous-niche :
1. NOM : un label court (2-4 mots) qui identifie clairement l'angle. Ce
   label sera utilise comme mot-cle de recherche, il doit etre concret et
   specifique (pas generique comme "information" ou "achat").
2. MOTS-CLES : 3 a 5 mots-cles de la liste ci-dessus qui appartiennent a
   cette sous-niche. Un mot-cle ne peut appartenir qu'a une seule sous-niche.
3. SCORE POTENTIEL : 0-10 (combine demande estimee + facilite d'execution
   pour un solo + monetisabilite). Sois discriminant : utilise toute la
   fourchette, pas uniquement 5-7.
4. MONETISATION : le modele le plus naturel pour cette sous-niche
   (e-commerce, dropshipping, affiliation, contenu/blog, service, SaaS,
   print-on-demand, formation, coaching, newsletter payante, outil gratuit).
5. RAISON : 1 phrase qui justifie le score et pointe un element concret.

REGLES :
- Trie les sous-niches par score_potentiel decroissant (meilleure en premier)
- Ne cree PAS de sous-niche fourre-tout type "autres" ou "divers"
- Si certains mots-cles ne rentrent dans aucune sous-niche coherente,
  ignore-les plutot que de forcer un regroupement artificiel
- Chaque sous-niche doit etre testable independamment (on peut creer un
  contenu ou un produit uniquement sur cet angle)

Ta reponse doit etre UN SEUL objet JSON, SANS texte autour :

{{
  "clusters": [
    {{
      "nom": "<label court 2-4 mots>",
      "mots_cles": ["<variante 1>", "<variante 2>", ...],
      "score_potentiel": <entier 0-10>,
      "monetisation": "<modele>",
      "raison": "<1 phrase>"
    }},
    ...
  ]
}}

Reponds UNIQUEMENT avec l'objet JSON. Pas de markdown, pas de commentaires."""


def _construire_prompt(
    mot_cle: str,
    variantes: list[str],
    contexte: str | None,
) -> str:
    """Construit le prompt avec la liste des variantes et le contexte."""
    contexte_bloc = ""
    if contexte:
        contexte_bloc = f"CONTEXTE DE LA NICHE : {contexte[:500]}"

    liste_variantes = "\n".join(f"- {v}" for v in variantes)

    return PROMPT_TEMPLATE.format(
        mot_cle=mot_cle,
        contexte_bloc=contexte_bloc,
        nb_variantes=len(variantes),
        liste_variantes=liste_variantes,
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
    Garantit que chaque cluster a les 5 champs attendus avec les bons types.
    Trie par score_potentiel decroissant (au cas ou Claude ne l'aurait pas fait).
    """
    clusters_raw = brut.get("clusters") or []
    if not isinstance(clusters_raw, list):
        clusters_raw = []

    clusters: list[dict[str, Any]] = []
    for c in clusters_raw[:7]:  # max 7 clusters
        if not isinstance(c, dict):
            continue

        nom = str(c.get("nom") or "").strip()[:80]
        if not nom:
            continue

        mots_cles = c.get("mots_cles") or []
        if not isinstance(mots_cles, list):
            mots_cles = []
        mots_cles = [
            str(m).strip()[:150]
            for m in mots_cles[:5]
            if isinstance(m, str) and m.strip()
        ]

        try:
            score = max(0, min(10, int(c.get("score_potentiel", 5))))
        except (ValueError, TypeError):
            score = 5

        monetisation = str(c.get("monetisation") or "").strip()[:100]
        raison = str(c.get("raison") or "").strip()[:300]

        clusters.append({
            "nom": nom,
            "mots_cles": mots_cles,
            "score_potentiel": score,
            "monetisation": monetisation,
            "raison": raison,
        })

    # Tri par score decroissant (securite si Claude n'a pas trie)
    clusters.sort(key=lambda x: x["score_potentiel"], reverse=True)

    return {
        "mot_cle": mot_cle,
        "nb_clusters": len(clusters),
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def cluster_keywords(
    mot_cle: str,
    keywords: dict[str, Any],
    contexte: str | None = None,
) -> dict[str, Any]:
    """
    Regroupe les variantes de expand_keywords() en 5-7 sous-niches testables.

    Args:
        mot_cle: mot-cle principal analyse
        keywords: dict retourne par expand_keywords() (contient par_intention)
        contexte: resume_fr de pipeline_ia pour affiner le clustering

    Returns:
        dict avec les cles :
        - mot_cle : str
        - nb_clusters : int (0-7)
        - clusters : list[dict] — chaque dict contient nom, mots_cles,
                     score_potentiel, monetisation, raison
        - erreur : str (present uniquement en cas d'echec)

    Tous les chemins d'erreur retournent un resultat vide (nb_clusters=0,
    clusters=[]) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    if not settings.ANTHROPIC_API_KEY:
        return _resultat_par_defaut(mot_cle, "cle_api_absente")

    # Extraire les variantes de expand_keywords
    variantes = _aplatir_variantes(keywords)
    if len(variantes) < 3:
        # Pas assez de variantes pour faire du clustering utile
        return _resultat_par_defaut(mot_cle, "variantes_insuffisantes")

    prompt = _construire_prompt(mot_cle, variantes, contexte)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
        logger.info(
            f"Keyword Clustering : reponse Haiku ({len(reponse)} chars) "
            f"pour '{mot_cle[:40]}'"
        )
    except Exception as e:
        logger.error(f"Keyword Clustering : erreur Claude — {e}")
        return _resultat_par_defaut(mot_cle, f"erreur_api: {e}")

    brut = _extraire_json(reponse)
    if brut is None:
        logger.warning(
            f"Keyword Clustering : JSON illisible ({reponse[:200]})"
        )
        return _resultat_par_defaut(mot_cle, "json_illisible")

    resultat = _normaliser_resultat(brut, mot_cle)
    logger.info(
        f"Keyword Clustering : {resultat['nb_clusters']} clusters "
        f"pour '{mot_cle[:40]}'"
    )
    return resultat
