"""
Service SERP Gap Detector — analyse concurrentielle SEO via Claude.

Pour un mot-cle donne :
1. Recupere le top 10 Google.fr via SerpAPI (demande 20, tronque a 10)
2. Etiquette heuristique du type de page (landing/blog/shop/forum/annuaire/
   wikipedia/pollution) comme contexte pour Claude
3. Envoie les 10 resultats a Claude Sonnet 4.5 avec un prompt expert SEO
4. Retourne un JSON structure : score_difficulte 0-10, verdict FACILE/MOYEN/
   DIFFICILE, verdict_raison, opportunites, faiblesses_detectees
5. Cache 7 jours dans cache_ia (source='serp_gap')

Fallbacks : pas de cle API, erreur SerpAPI, erreur Claude, JSON illisible ->
resultat neutre par defaut pour ne jamais crasher le mode Analyse.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import anthropic
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# On demande 20 resultats et on tronque a 10 pour lisser les cas ou Google
# renvoie 8 ou 9 items (shopping/knowledge card qui remplace un slot organique)
SERPAPI_NUM_RESULTS = 20
TOP_N = 10

MODELE_CLAUDE = "claude-sonnet-4-5"
MAX_TOKENS = 1500

# Cache 7 jours : les SERP bougent lentement, l'utilisatrice ne refait pas
# une analyse sur le meme mot-cle dans la meme semaine
CACHE_TTL_HEURES = 24 * 7

# Troncature du snippet envoye a Claude (economie de tokens)
MAX_SNIPPET_CHARS = 150

VERDICTS_VALIDES = {"FACILE", "MOYEN", "DIFFICILE"}

CACHE_SOURCE = "serp_gap"


# ---------------------------------------------------------------------------
# Patterns heuristiques detection type de page
# ---------------------------------------------------------------------------

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


def _detecter_type_page(url: str) -> str:
    """
    Etiquette heuristique du type de page, envoyee a Claude comme contexte.
    Claude reste libre de l'override dans son jugement si l'URL est ambigue.
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
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec (pas de cle API, erreur, JSON illisible)."""
    return {
        "mot_cle": mot_cle,
        "score_difficulte": 5,
        "verdict": "MOYEN",
        "verdict_raison": f"Analyse SERP non disponible ({raison}) — verdict neutre.",
        "opportunites": [],
        "faiblesses_detectees": [],
        "top_10": [],
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia de pipeline_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    """
    Hash stable du mot-cle normalise pour la cle de cache.
    Un prefixe 'serp_gap:' evite toute collision avec les hashs de pipeline_ia
    (qui utilisent titre+contenu+thematiques, pas juste un mot-cle).
    """
    payload = f"serp_gap:{mot_cle.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lire_cache(session: AsyncSession, hash_contenu: str) -> dict[str, Any] | None:
    """Recupere un resultat en cache si valide (non expire)."""
    try:
        result = await session.execute(
            text(
                "SELECT resultat FROM cache_ia "
                "WHERE hash_contenu = :h AND expires_at > NOW() "
                "LIMIT 1"
            ),
            {"h": hash_contenu},
        )
        row = result.first()
        if row is None:
            return None
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.debug(f"SERP Gap : cache indisponible (lecture) — {e}")
        return None


async def _ecrire_cache(
    session: AsyncSession,
    hash_contenu: str,
    resultat: dict[str, Any],
) -> None:
    """Ecrit un resultat en cache avec TTL 7 jours."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HEURES)
    try:
        await session.execute(
            text(
                "INSERT INTO cache_ia (hash_contenu, source, resultat, expires_at) "
                "VALUES (:h, :s, CAST(:r AS JSONB), :e) "
                "ON CONFLICT (hash_contenu) DO UPDATE "
                "SET source = EXCLUDED.source, "
                "    resultat = EXCLUDED.resultat, "
                "    expires_at = EXCLUDED.expires_at"
            ),
            {
                "h": hash_contenu,
                "s": CACHE_SOURCE,
                "r": json.dumps(resultat, ensure_ascii=False),
                "e": expires_at,
            },
        )
        await session.commit()
    except Exception as e:
        logger.debug(f"SERP Gap : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Appel SerpAPI
# ---------------------------------------------------------------------------

async def _fetcher_top_10(mot_cle: str) -> list[dict]:
    """
    Appelle SerpAPI google engine et retourne les organic_results tronques a TOP_N.
    Leve RuntimeError si SERPAPI_KEY absente, Exception si erreur reseau.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

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
# Formatage des resultats pour Claude
# ---------------------------------------------------------------------------

def _formatter_resultats_pour_claude(organic_results: list[dict]) -> str:
    """
    Construit un bloc texte compact pour Claude.
    4 lignes par resultat : position+domaine+type / title / url / snippet.
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

        type_page = _detecter_type_page(url)

        lignes.append(f"[{i}] {domaine} ({type_page})")
        lignes.append(f"    Title : {title}")
        lignes.append(f"    URL : {url}")
        if snippet:
            lignes.append(f"    Snippet : {snippet}")
        lignes.append("")

    return "\n".join(lignes)


def _extraire_top_10_structure(organic_results: list[dict]) -> list[dict]:
    """
    Construit la liste structuree du top 10 pour le stockage (cache + BDD).
    Plus concis que les organic_results bruts de SerpAPI.
    """
    top = []
    for i, raw in enumerate(organic_results, 1):
        url = (raw.get("link") or "").strip()
        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""

        top.append({
            "position": i,
            "titre": (raw.get("title") or "").strip(),
            "url": url,
            "domaine": domaine,
            "type_page": _detecter_type_page(url),
            "snippet": (raw.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS],
        })
    return top


# ---------------------------------------------------------------------------
# Prompt Claude
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
# Parsing JSON robuste
# ---------------------------------------------------------------------------

def _extraire_json(texte: str) -> dict | None:
    """Extrait le premier objet JSON valide avec 3 fallbacks (direct, regex, fences)."""
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

def _normaliser_resultat_claude(
    brut: dict,
    mot_cle: str,
    top_10: list[dict],
) -> dict[str, Any]:
    """
    Valide et clamp le JSON retourne par Claude.
    Ajoute mot_cle et top_10 dans le resultat final pour tracabilite.
    """
    defaut = _resultat_par_defaut(mot_cle, "normalisation")

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
        "mot_cle": mot_cle,
        "score_difficulte": _clamp(brut.get("score_difficulte")),
        "verdict": verdict,
        "verdict_raison": str(brut.get("verdict_raison") or "")[:500],
        "opportunites": opportunites,
        "faiblesses_detectees": faiblesses,
        "top_10": top_10,
    }


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser_serp(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse SERP d'un mot-cle : top 10 Google + jugement expert via Claude.

    Args:
        mot_cle: mot-cle a analyser (sera normalise pour le cache)
        session: session SQLAlchemy async pour le cache (optionnelle).
            Si fournie, lit/ecrit dans cache_ia avec TTL 7 jours.

    Returns:
        dict avec les cles :
        - mot_cle : str (le mot-cle analyse)
        - score_difficulte : int (0-10)
        - verdict : str (FACILE / MOYEN / DIFFICILE)
        - verdict_raison : str (1-2 phrases)
        - opportunites : list[str] (2-5 pistes actionnables)
        - faiblesses_detectees : list[str] (0-5 faiblesses)
        - top_10 : list[dict] (snapshot du top 10 structure)

    Tous les chemins d'erreur retournent un resultat par defaut neutre
    (score 5, verdict MOYEN) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ----------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"SERP Gap : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- Appel SerpAPI --------------------------------------------------------
    try:
        organic_results = await _fetcher_top_10(mot_cle)
    except RuntimeError as e:
        logger.warning(f"SERP Gap : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"SERP Gap : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_erreur")

    if not organic_results:
        logger.info(f"SERP Gap : aucun resultat SerpAPI pour '{mot_cle[:40]}'")
        return _resultat_par_defaut(mot_cle, "aucun_resultat_serp")

    top_10 = _extraire_top_10_structure(organic_results)

    # --- Cle API Claude absente : fallback avec top 10 brut -------------------
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("SERP Gap : ANTHROPIC_API_KEY non configuree")
        resultat = _resultat_par_defaut(mot_cle, "cle_api_claude_absente")
        resultat["top_10"] = top_10
        return resultat

    # --- Appel Claude ---------------------------------------------------------
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
        logger.info(f"SERP Gap : reponse Claude ({len(reponse)} chars) pour '{mot_cle[:40]}'")
    except Exception as e:
        logger.error(f"SERP Gap : erreur Claude — {e}")
        resultat = _resultat_par_defaut(mot_cle, "erreur_api_claude")
        resultat["top_10"] = top_10
        return resultat

    # --- Parsing + normalisation ----------------------------------------------
    brut = _extraire_json(reponse)
    if brut is None:
        logger.warning(f"SERP Gap : JSON illisible — fallback ({reponse[:200]})")
        resultat = _resultat_par_defaut(mot_cle, "json_illisible")
        resultat["top_10"] = top_10
        return resultat

    resultat = _normaliser_resultat_claude(brut, mot_cle, top_10)

    # --- Ecriture cache 7 jours -----------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
