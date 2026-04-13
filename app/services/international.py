"""
Service Score Potentiel International — detection de marches vierges a
l'etranger via SerpAPI multi-pays.

Pour un mot-cle FR :
1. Traduit en 4 langues via Claude Haiku (1 appel : EN/DE/ES/IT)
2. Lance 4 appels SerpAPI paralleles avec gl= et hl= par pays
3. Scoring deterministe par marche : VIERGE / EMERGENT / MATURE
4. Retourne les marches tries par score decroissant (meilleur en premier)
5. Cache 30 jours dans cache_ia (source='international')

Cout : ~$0.010 par analyse (1 Haiku + 4 SerpAPI). Pas de Claude Sonnet.
"""

import asyncio
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

MODELE_TRADUCTION = "claude-haiku-4-5-20251001"

CACHE_TTL_HEURES = 24 * 30
CACHE_SOURCE = "international"

# On ne demande que 5 resultats par marche (suffisant pour scorer la qualite
# du top et detecter si c'est vierge/mature — pas besoin de 10-20)
SERPAPI_NUM_RESULTS = 5

# Marches cibles : code, nom affiche, gl SerpAPI, hl SerpAPI
MARCHES = [
    {"code": "en", "pays": "USA", "gl": "us", "hl": "en"},
    {"code": "de", "pays": "Allemagne", "gl": "de", "hl": "de"},
    {"code": "es", "pays": "Espagne", "gl": "es", "hl": "es"},
    {"code": "it", "pays": "Italie", "gl": "it", "hl": "it"},
]

VERDICTS_VALIDES = {"VIERGE", "EMERGENT", "MATURE"}

# Patterns pour detecter les pages editoriales (blog/article/guide)
PATTERNS_EDITORIAL = re.compile(
    r"/(blog|article|actualites?|news|post|posts|guide|how-to|ratgeber|guia|como)/",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec."""
    return {
        "mot_cle": mot_cle,
        "nb_marches_analyses": 0,
        "marches": [],
        "recommandation": f"Analyse internationale non disponible ({raison}).",
        "meilleurs_marches": [],
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    payload = f"international:{mot_cle.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lire_cache(
    session: AsyncSession, hash_contenu: str,
) -> dict[str, Any] | None:
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
        logger.debug(f"International : cache indisponible (lecture) — {e}")
        return None


async def _ecrire_cache(
    session: AsyncSession,
    hash_contenu: str,
    resultat: dict[str, Any],
) -> None:
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
        logger.debug(f"International : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Traduction du mot-cle en 4 langues via Claude Haiku
# ---------------------------------------------------------------------------

PROMPT_TRADUCTION = """Traduis ce mot-cle commercial du francais vers 4 langues.
Retourne le terme le plus naturel qu'un acheteur taperait dans Google
dans chaque pays (pas une traduction litterale, le terme commercial usuel).

Mot-cle FR : {mot_cle}

Reponds UNIQUEMENT avec un objet JSON, rien d'autre :

{{"en": "<terme anglais>", "de": "<terme allemand>", "es": "<terme espagnol>", "it": "<terme italien>"}}"""


async def _traduire_mot_cle(mot_cle: str) -> dict[str, str] | None:
    """
    Traduit un mot-cle FR en 4 langues (EN/DE/ES/IT) via Claude Haiku.
    Retourne un dict {code_langue: traduction} ou None si echec.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None

    prompt = PROMPT_TRADUCTION.format(mot_cle=mot_cle)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_TRADUCTION,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
    except Exception as e:
        logger.error(f"International : erreur traduction Haiku — {e}")
        return None

    # Parsing robuste
    try:
        data = json.loads(reponse)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", reponse, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    # Valider que les 4 langues sont presentes
    traductions = {}
    for code in ("en", "de", "es", "it"):
        val = data.get(code)
        if isinstance(val, str) and val.strip():
            traductions[code] = val.strip()
        else:
            traductions[code] = mot_cle  # fallback sur le FR

    return traductions


# ---------------------------------------------------------------------------
# Appels SerpAPI paralleles (1 par marche)
# ---------------------------------------------------------------------------

async def _fetcher_un_marche(
    client: httpx.AsyncClient,
    mot_cle_traduit: str,
    marche: dict,
) -> dict[str, Any]:
    """
    Execute une requete SerpAPI pour un marche et retourne les signaux.
    Ne raise jamais — retourne un dict avec erreur=str si probleme.
    """
    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": mot_cle_traduit,
        "gl": marche["gl"],
        "hl": marche["hl"],
        "num": SERPAPI_NUM_RESULTS,
    }

    base = {
        "pays": marche["pays"],
        "code": marche["code"],
        "mot_cle_traduit": mot_cle_traduit,
        "total_resultats": 0,
        "nb_top5_editorial": 0,
        "nb_ads": 0,
        "erreur": None,
    }

    try:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        base["erreur"] = str(e)[:200]
        return base

    # Total results
    search_info = data.get("search_information") or {}
    total = search_info.get("total_results", 0)
    try:
        base["total_resultats"] = int(total) if total else 0
    except (ValueError, TypeError):
        base["total_resultats"] = 0

    # Nb editorial dans le top 5
    organic = data.get("organic_results") or []
    nb_editorial = 0
    for item in organic[:5]:
        url = (item.get("link") or "").lower()
        if PATTERNS_EDITORIAL.search(url):
            nb_editorial += 1
    base["nb_top5_editorial"] = nb_editorial

    # Nb ads
    ads = data.get("ads") or []
    base["nb_ads"] = len(ads)

    return base


async def _fetcher_tous_marches(
    mot_cle: str,
    traductions: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Lance 4 requetes SerpAPI en parallele (1 par marche).
    Retourne la liste des resultats par marche.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

    async with httpx.AsyncClient(timeout=30) as client:
        resultats = await asyncio.gather(
            *(
                _fetcher_un_marche(
                    client,
                    traductions.get(m["code"], mot_cle),
                    m,
                )
                for m in MARCHES
            ),
        )

    return list(resultats)


# ---------------------------------------------------------------------------
# Scoring deterministe par marche
# ---------------------------------------------------------------------------

def _scorer_marche(marche: dict) -> tuple[int, str, str]:
    """
    Score un marche et retourne (score 0-10, verdict, raison).

    Regles :
    - VIERGE  (7-10) : < 50K resultats ET 0 ads ET < 2 editorial
    - EMERGENT (4-6) : 50K-500K resultats OU 1-3 ads
    - MATURE  (0-3)  : > 500K resultats OU 4+ ads OU 4+ editorial
    """
    if marche.get("erreur"):
        return 0, "MATURE", f"Erreur d'analyse ({marche['erreur'][:80]})"

    total = marche.get("total_resultats", 0)
    nb_ads = marche.get("nb_ads", 0)
    nb_edit = marche.get("nb_top5_editorial", 0)
    mot_cle_t = marche.get("mot_cle_traduit", "")

    # Score de base
    score = 5

    # Ajustements sur total_results
    if total < 10000:
        score += 4
    elif total < 50000:
        score += 2
    elif total < 500000:
        score += 0
    elif total < 1000000:
        score -= 2
    else:
        score -= 3

    # Ajustements sur ads
    if nb_ads == 0:
        score += 2  # Aucun annonceur = marche pas encore monetise
    elif nb_ads <= 3:
        score -= 1
    else:
        score -= 2  # 4+ annonceurs = marche mature

    # Ajustements sur editorial
    if nb_edit == 0:
        score += 1  # Pas de contenu editorial = facile a positionner
    elif nb_edit >= 4:
        score -= 2  # Top 5 sature par de l'editorial

    # Clamp 0-10
    score = max(0, min(10, score))

    # Verdict depuis le score
    if score >= 7:
        verdict = "VIERGE"
    elif score >= 4:
        verdict = "EMERGENT"
    else:
        verdict = "MATURE"

    # Raison
    parts = [f"{total:,} resultats".replace(",", " ")]
    if nb_ads > 0:
        parts.append(f"{nb_ads} annonceur{'s' if nb_ads > 1 else ''}")
    else:
        parts.append("0 annonceur")
    if nb_edit > 0:
        parts.append(f"{nb_edit} page{'s' if nb_edit > 1 else ''} editoriale{'s' if nb_edit > 1 else ''} dans le top 5")
    else:
        parts.append("0 editorial dans le top 5")

    raison = f"{', '.join(parts)}."

    return score, verdict, raison


def _generer_recommandation(marches_scores: list[dict]) -> str:
    """Genere une recommandation globale a partir des marches scores."""
    vierges = [m for m in marches_scores if m["verdict"] == "VIERGE"]
    emergents = [m for m in marches_scores if m["verdict"] == "EMERGENT"]

    if vierges:
        noms = " et ".join(m["pays"] for m in vierges[:2])
        return f"{noms} {'sont vierges' if len(vierges) > 1 else 'est vierge'} — opportunite de lancement prioritaire."
    if emergents:
        noms = " et ".join(m["pays"] for m in emergents[:2])
        return f"{noms} {'sont emergents' if len(emergents) > 1 else 'est emergent'} — marche en developpement, bon timing."
    return "Tous les marches analyses sont matures — differenciation forte necessaire pour se positionner a l'international."


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser_international(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse le potentiel international d'un mot-cle sur 4 marches
    (USA, Allemagne, Espagne, Italie).

    Args:
        mot_cle: mot-cle FR a analyser
        session: session SQLAlchemy async pour le cache (optionnelle).
            Si fournie, lit/ecrit dans cache_ia avec TTL 30 jours.

    Returns:
        dict avec les cles :
        - mot_cle : str
        - nb_marches_analyses : int (4)
        - marches : list[dict] (tries par score decroissant)
        - recommandation : str (1 phrase)
        - meilleurs_marches : list[str] (codes des marches VIERGE/EMERGENT)

    Fallback neutre (0 marche, liste vide) si erreur.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ---------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"International : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- Traduction du mot-cle en 4 langues ----------------------------------
    if not settings.ANTHROPIC_API_KEY:
        return _resultat_par_defaut(mot_cle, "cle_api_absente")

    traductions = await _traduire_mot_cle(mot_cle)
    if traductions is None:
        return _resultat_par_defaut(mot_cle, "traduction_echouee")

    logger.info(
        f"International : traductions pour '{mot_cle[:30]}' : "
        + ", ".join(f"{k}='{v}'" for k, v in traductions.items())
    )

    # --- 4 appels SerpAPI paralleles -----------------------------------------
    try:
        resultats_marches = await _fetcher_tous_marches(mot_cle, traductions)
    except RuntimeError as e:
        logger.warning(f"International : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"International : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_erreur")

    # --- Scoring par marche --------------------------------------------------
    marches_scores: list[dict[str, Any]] = []
    for marche_data in resultats_marches:
        score, verdict, raison = _scorer_marche(marche_data)
        marches_scores.append({
            "pays": marche_data["pays"],
            "code": marche_data["code"],
            "mot_cle_traduit": marche_data["mot_cle_traduit"],
            "total_resultats": marche_data["total_resultats"],
            "nb_top5_editorial": marche_data["nb_top5_editorial"],
            "nb_ads": marche_data["nb_ads"],
            "score": score,
            "verdict": verdict,
            "raison": raison,
        })

    # Tri par score decroissant (meilleur marche en premier)
    marches_scores.sort(key=lambda x: x["score"], reverse=True)

    # Meilleurs marches = VIERGE ou EMERGENT
    meilleurs = [
        m["code"] for m in marches_scores
        if m["verdict"] in ("VIERGE", "EMERGENT")
    ]

    recommandation = _generer_recommandation(marches_scores)

    resultat = {
        "mot_cle": mot_cle,
        "nb_marches_analyses": len(marches_scores),
        "marches": marches_scores,
        "recommandation": recommandation,
        "meilleurs_marches": meilleurs,
    }

    logger.info(
        f"International : '{mot_cle[:30]}' -> "
        + ", ".join(f"{m['code']}={m['verdict']}({m['score']})" for m in marches_scores)
    )

    # --- Ecriture cache 30 jours ---------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
