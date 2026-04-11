"""
Dry-run du SERP Gap Detector — version Claude-powered.

Flux :
1. SerpAPI : recupere le top 20 Google.fr pour un mot-cle, tronque a 10
2. Heuristique locale : etiquette chaque resultat avec un type de page
   (landing/blog/shop/forum/annuaire/wikipedia) pour aider Claude
3. Claude Sonnet 4.5 : analyse les 10 resultats et retourne un JSON
   structure : score_difficulte, verdict, raison, opportunites, faiblesses
4. Rapport texte lisible

But : valider le prompt et le format de retour avant de coder serp_gap.py.
Zero BDD, zero cache. Chaque run = 1 appel SerpAPI + 1 appel Claude.

Usage :
    docker compose exec app python -m scripts.test_serp_gap "mot cle"
    docker compose exec app python -m scripts.test_serp_gap   (defaut ci-dessous)
"""

import asyncio
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

import anthropic
import httpx

from app.config import settings

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# On demande 20 resultats a Google (robuste aux SERP tronquees) et on tronque
# a 10 pour l'analyse — permet de lisser les cas ou Google renvoie 8 ou 9 items
# parce qu'une shopping/knowledge card remplace un slot organique.
SERPAPI_NUM_RESULTS = 20
TOP_N = 10

MODELE_CLAUDE = "claude-sonnet-4-5"
MAX_TOKENS = 1500

MOT_CLE_DEFAUT = "automatisation comptable"

# Longueur max du snippet envoye a Claude (economie de tokens)
MAX_SNIPPET_CHARS = 150


# ---------- Patterns heuristiques detection type de page -------------------

PATTERNS_BLOG = re.compile(r"/(blog|article|actualites?|news|post|posts)/", re.IGNORECASE)
PATTERNS_SHOP = re.compile(r"/(produit|product|p|shop|boutique|store)/", re.IGNORECASE)
DOMAINES_FORUM = ("reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com")
PATTERNS_FORUM_URL = re.compile(r"(forum|discussion|thread|topic)", re.IGNORECASE)
DOMAINES_ANNUAIRE = (
    "pagesjaunes.fr", "yelp.fr", "yelp.com", "trustpilot.com", "trustpilot.fr",
    "societe.com", "mappy.com", "118000.fr", "mappy.fr",
)
DOMAINES_WIKIPEDIA = ("wikipedia.org", "wikimedia.org")
DOMAINES_POLLUTION = (
    "pinterest.com", "pinterest.fr", "dailymotion.com", "instagram.com",
    "tiktok.com", "facebook.com", "youtube.com",
)


def detecter_type_page(url: str) -> str:
    """
    Etiquette heuristique du type de page, envoyee a Claude comme contexte.
    Claude reste libre de l'override dans son jugement si l'URL est ambigue.

    Ordre de priorite (premier match gagne) :
      wikipedia > pollution (reseaux sociaux) > forum > annuaire > blog > shop > landing
    """
    if not url:
        return "landing"

    try:
        parsed = urlparse(url)
        netloc = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        return "landing"

    if any(d in netloc for d in DOMAINES_WIKIPEDIA):
        return "wikipedia"
    if any(d in netloc for d in DOMAINES_POLLUTION):
        return "pollution"
    if any(d in netloc for d in DOMAINES_FORUM) or PATTERNS_FORUM_URL.search(netloc):
        return "forum"
    if any(d in netloc for d in DOMAINES_ANNUAIRE):
        return "annuaire"
    if PATTERNS_BLOG.search(path):
        return "blog"
    if PATTERNS_SHOP.search(path) or netloc.startswith("shop."):
        return "shop"
    return "landing"


# ---------------------------------------------------------------------------
# 1. Appel SerpAPI — top 20 tronque a 10
# ---------------------------------------------------------------------------

async def fetcher_top_10(mot_cle: str) -> list[dict]:
    """Appelle SerpAPI google engine et retourne les organic_results tronques a TOP_N."""
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree dans .env")

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": mot_cle,
        "gl": "fr",
        "hl": "fr",
        "num": SERPAPI_NUM_RESULTS,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    return data.get("organic_results", [])[:TOP_N]


# ---------------------------------------------------------------------------
# 2. Formatage des resultats pour le prompt Claude
# ---------------------------------------------------------------------------

def _formatter_resultats_pour_claude(organic_results: list[dict]) -> str:
    """
    Construit un bloc texte compact et lisible pour Claude.
    4 lignes par resultat : position+domaine+type / title / url / snippet tronque.
    """
    lignes = []
    for i, raw in enumerate(organic_results, 1):
        title = (raw.get("title") or "").strip()
        url = (raw.get("link") or "").strip()
        snippet = (raw.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS]

        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""

        type_page = detecter_type_page(url)

        lignes.append(f"[{i}] {domaine} ({type_page})")
        lignes.append(f"    Title : {title}")
        lignes.append(f"    URL : {url}")
        if snippet:
            lignes.append(f"    Snippet : {snippet}")
        lignes.append("")  # ligne vide entre resultats

    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# 3. Prompt Claude
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """Tu es un expert SEO senior specialise dans l'analyse concurrentielle
pour des consultants independants qui cherchent des niches exploitables.

MOT-CLE ANALYSE : {mot_cle}

TOP {n} RESULTATS GOOGLE.FR (par position) :
{resultats_formates}

Ta mission : evaluer la difficulte de positionnement sur ce mot-cle pour un
createur de contenu solo. L'objectif n'est PAS de juger si les pages existantes
sont bonnes — c'est de juger si un guide editorial bien fait peut les doubler.

CRITERES D'EVALUATION :

1. Qualite des titles (mot-cle present ? longueur optimale 30-60 chars ?
   variantes semantiques ? titles bacles ou travailles ?)

2. Types de pages dominants :
   - landing produit / marque = concurrence moyenne (depend de la qualite)
   - blog / article de fond = concurrence editoriale directe
   - shop generique = concurrence faible (page produit automatique)
   - forum / Quora / Reddit = tres faible (Google cherche de la reference)
   - annuaire / pages jaunes = tres faible
   - wikipedia = intouchable sur ce slot, mais pas bloquant
   - dailymotion / youtube / instagram / pinterest = tres faible (pollution)

3. Diversite des domaines : un top 10 domine par 2-3 marques = marche captif,
   plus difficile. Top 10 eparpille = plus accessible.

4. Signaux de niche bacle : beaucoup de titles genre "Amazon.fr : X" ou
   "[Marque] specialiste" sans effort editorial = opportunite forte.

5. Signaux de niche travaillee : titles avec benefice clair, variantes long
   tail, guides comparatifs en top 10 = opportunite faible.

ATTENTION aux pieges :
- Un title court comme "Pennylane" ou "Bebe Nacre" peut etre une marque dominante
  (force) ou un site bacle (faiblesse) — regarde le domaine et la position.
- Un article de blog en top 5 d'une niche produit = le contenu editorial est
  rare, opportunite forte pour un guide concurrent.
- Amazon/Cdiscount en top 3 sur une niche tres specifique = Google n'a pas
  trouve mieux, le marche est sous-exploite editorialement.

Ta reponse doit etre UN SEUL objet JSON, SANS texte autour, avec cette structure
EXACTE :

{{
  "score_difficulte": <entier 0-10, 0=tres facile, 10=mission impossible>,
  "verdict": "<FACILE si score 0-3, MOYEN si 4-6, DIFFICILE si 7-10>",
  "verdict_raison": "<1-2 phrases qui expliquent le score en pointant les elements concrets du top 10>",
  "opportunites": [
    "<piste actionnable 1, max 100 chars>",
    "<piste actionnable 2>",
    "..."
  ],
  "faiblesses_detectees": [
    "<faiblesse concrete observee dans le top 10, max 80 chars>",
    "..."
  ]
}}

Donne 2-5 opportunites actionnables maximum (angles editoriaux, formats de
contenu, sous-niches a creuser, comparatifs manquants).
Donne 0-5 faiblesses_detectees sur les pages en place (titles bacles, manque
de contenu, sites de mauvaise qualite).

Reponds UNIQUEMENT avec l'objet JSON. Pas de markdown, pas de commentaires."""


def _construire_prompt(mot_cle: str, resultats_formates: str, n: int) -> str:
    return PROMPT_TEMPLATE.format(
        mot_cle=mot_cle,
        n=n,
        resultats_formates=resultats_formates,
    )


# ---------------------------------------------------------------------------
# 4. Parsing JSON robuste (copie du pattern de pipeline_ia._extraire_json)
# ---------------------------------------------------------------------------

def _extraire_json(texte: str) -> dict | None:
    """Extrait le premier objet JSON valide avec 3 fallbacks."""
    if not texte:
        return None
    # 1. Parse direct
    try:
        return json.loads(texte.strip())
    except json.JSONDecodeError:
        pass
    # 2. Regex {...} greedy
    match = re.search(r"\{.*\}", texte, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # 3. Nettoyage fences markdown
    nettoye = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.MULTILINE)
    try:
        return json.loads(nettoye)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 5. Normalisation du retour Claude
# ---------------------------------------------------------------------------

VERDICTS_VALIDES = {"FACILE", "MOYEN", "DIFFICILE"}


def _resultat_par_defaut(raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec (pas de cle API, erreur Claude, JSON illisible)."""
    return {
        "score_difficulte": 5,
        "verdict": "MOYEN",
        "verdict_raison": f"Analyse SERP non disponible ({raison}) — verdict neutre.",
        "opportunites": [],
        "faiblesses_detectees": [],
    }


def _normaliser_resultat_claude(brut: dict, raison_defaut: str = "normalisation") -> dict[str, Any]:
    """
    Valide et clamp le JSON retourne par Claude :
    - score_difficulte en 0-10
    - verdict dans {FACILE, MOYEN, DIFFICILE}
    - opportunites tronquees a 5
    - faiblesses_detectees tronquees a 5
    - champs manquants remplaces par des defauts
    """
    defaut = _resultat_par_defaut(raison_defaut)

    def _clamp(v, mini=0, maxi=10) -> int:
        try:
            return max(mini, min(maxi, int(v)))
        except (TypeError, ValueError):
            return 5

    verdict = str(brut.get("verdict", "")).upper().strip()
    if verdict not in VERDICTS_VALIDES:
        verdict = defaut["verdict"]

    opportunites = brut.get("opportunites", [])
    if not isinstance(opportunites, list):
        opportunites = []
    opportunites = [str(o)[:200] for o in opportunites[:5]]

    faiblesses = brut.get("faiblesses_detectees", [])
    if not isinstance(faiblesses, list):
        faiblesses = []
    faiblesses = [str(f)[:200] for f in faiblesses[:5]]

    return {
        "score_difficulte": _clamp(brut.get("score_difficulte")),
        "verdict": verdict,
        "verdict_raison": str(brut.get("verdict_raison") or "")[:500],
        "opportunites": opportunites,
        "faiblesses_detectees": faiblesses,
    }


# ---------------------------------------------------------------------------
# 6. Appel Claude
# ---------------------------------------------------------------------------

async def analyser_via_claude(mot_cle: str, organic_results: list[dict]) -> dict[str, Any]:
    """
    Envoie le top 10 a Claude et retourne un resultat normalise.
    Tous les chemins d'erreur retombent sur _resultat_par_defaut.
    """
    if not settings.ANTHROPIC_API_KEY:
        return _resultat_par_defaut("cle_api_absente")

    if not organic_results:
        return _resultat_par_defaut("aucun_resultat_serp")

    resultats_formates = _formatter_resultats_pour_claude(organic_results)
    prompt = _construire_prompt(mot_cle, resultats_formates, len(organic_results))

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
    except Exception as e:
        return _resultat_par_defaut(f"erreur_api: {e}")

    brut = _extraire_json(reponse)
    if brut is None:
        return _resultat_par_defaut("json_illisible")

    return _normaliser_resultat_claude(brut)


# ---------------------------------------------------------------------------
# 7. Rendu texte du rapport
# ---------------------------------------------------------------------------

def rendre_rapport(
    mot_cle: str,
    organic_results: list[dict],
    analyse: dict[str, Any],
) -> None:
    print("=" * 76)
    print("SERP Gap Detector — dry-run (Claude-powered)")
    print("=" * 76)
    print(f"Mot-cle          : '{mot_cle}'")
    print(f"Top {TOP_N} google.fr : {len(organic_results)} resultats analyses")
    print()

    verdict = analyse["verdict"]
    couleur = {
        "FACILE": "[FACILE]",
        "MOYEN": "[MOYEN]",
        "DIFFICILE": "[DIFFICILE]",
    }.get(verdict, verdict)
    print(f"Score difficulte : {analyse['score_difficulte']}/10 — {couleur}")
    print(f"Raison           : {analyse['verdict_raison']}")
    print()

    # Opportunites
    opportunites = analyse.get("opportunites") or []
    if opportunites:
        print("-" * 76)
        print(f"Opportunites ({len(opportunites)}) :")
        for o in opportunites:
            print(f"  + {o}")
        print()

    # Faiblesses detectees
    faiblesses = analyse.get("faiblesses_detectees") or []
    if faiblesses:
        print("-" * 76)
        print(f"Faiblesses detectees ({len(faiblesses)}) :")
        for f in faiblesses:
            print(f"  - {f}")
        print()

    # Top 10 brut pour debug
    print("=" * 76)
    print("Top 10 brut (pour debug / traçabilite)")
    print("=" * 76)
    for i, raw in enumerate(organic_results, 1):
        title = (raw.get("title") or "")[:70]
        url = (raw.get("link") or "")[:90]
        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""
        type_page = detecter_type_page(url)
        print(f"[{i:>2}] {domaine:<35} ({type_page})")
        print(f"     {title}")
    print()
    print("Dry-run termine. Aucune donnee persistee, aucun cache.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    mot_cle = sys.argv[1] if len(sys.argv) > 1 else MOT_CLE_DEFAUT

    try:
        organic_results = await fetcher_top_10(mot_cle)
    except RuntimeError as e:
        print(f"ERREUR : {e}")
        return
    except Exception as e:
        print(f"ERREUR SerpAPI : {e}")
        return

    if not organic_results:
        print(f"Aucun resultat organique retourne pour '{mot_cle}'.")
        return

    print(f"SerpAPI : {len(organic_results)} resultats collectes, analyse Claude en cours...")
    print()
    analyse = await analyser_via_claude(mot_cle, organic_results)
    rendre_rapport(mot_cle, organic_results, analyse)


if __name__ == "__main__":
    asyncio.run(main())
