"""
Service Amazon Market — verification du volume de produits Amazon FR
sur une niche via SerpAPI engine=amazon.

Pour un mot-cle donne :
1. Appel SerpAPI engine=amazon sur amazon.fr
2. Extraction : total_results, prix top 5, avis top 5, top 3 produits
3. Scoring deterministe : MICRO / NICHE / ETABLI / SATURE
4. Cache 30 jours dans cache_ia (source='amazon_market')

Cout : ~$0.002 par appel (1 SerpAPI). Pas de Claude.

NOTE : initialement prevu avec Amazon PAAPI, mais l'acces PAAPI necessite
3 ventes qualifiantes sur le compte Associates. SerpAPI engine=amazon
est le fallback fonctionnel immediat avec les memes donnees.
"""

import hashlib
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

CACHE_TTL_HEURES = 24 * 30
CACHE_SOURCE = "amazon_market"

VERDICTS_VALIDES = {"MICRO", "NICHE", "ETABLI", "SATURE"}


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec."""
    return {
        "mot_cle": mot_cle,
        "score_amazon": 0,
        "verdict": "MICRO",
        "verdict_raison": f"Analyse Amazon non disponible ({raison}).",
        "total_resultats": 0,
        "prix_moyen": None,
        "avis_median": None,
        "top_produits": [],
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    payload = f"amazon_market:{mot_cle.strip().lower()}"
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
        logger.debug(f"Amazon Market : cache indisponible (lecture) — {e}")
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
        logger.debug(f"Amazon Market : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Appel SerpAPI engine=amazon
# ---------------------------------------------------------------------------

async def _fetcher_amazon(mot_cle: str) -> dict[str, Any]:
    """
    Appelle SerpAPI engine=amazon sur amazon.fr.

    Retourne un dict avec :
      - total_resultats : int
      - items : list[dict] (asin, titre, prix, nb_avis, note, url)

    Leve RuntimeError si SERPAPI_KEY absente.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "amazon",
        "amazon_domain": "amazon.fr",
        "k": mot_cle,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    # Total results
    search_info = data.get("search_information") or {}
    total = 0
    total_raw = search_info.get("total_results")
    if total_raw is not None:
        try:
            total = int(total_raw)
        except (ValueError, TypeError):
            pass

    # Items
    organic = data.get("organic_results") or []
    items: list[dict] = []
    for item in organic:
        asin = item.get("asin") or ""
        titre = (item.get("title") or "")[:200]
        url = item.get("link") or ""

        # Prix : SerpAPI retourne price comme dict ou string
        prix = None
        price_raw = item.get("price")
        if isinstance(price_raw, dict):
            val = price_raw.get("value") or price_raw.get("raw")
            if val is not None:
                try:
                    prix = float(str(val).replace(",", ".").replace("€", "").replace(" ", "").strip())
                except (ValueError, TypeError):
                    pass
        elif isinstance(price_raw, (int, float)):
            prix = float(price_raw)
        elif isinstance(price_raw, str):
            try:
                prix = float(price_raw.replace(",", ".").replace("€", "").replace(" ", "").strip())
            except (ValueError, TypeError):
                pass

        # Avis
        nb_avis = None
        rating_raw = item.get("reviews")
        if rating_raw is not None:
            try:
                nb_avis = int(rating_raw)
            except (ValueError, TypeError):
                pass

        # Note
        note = None
        note_raw = item.get("rating")
        if note_raw is not None:
            try:
                note = float(note_raw)
            except (ValueError, TypeError):
                pass

        items.append({
            "asin": asin,
            "titre": titre,
            "prix": prix,
            "nb_avis": nb_avis,
            "note": note,
            "url": url,
        })

    return {
        "total_resultats": total,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Scoring deterministe
# ---------------------------------------------------------------------------

def _scorer_marche(
    total: int,
    prix_moyen: float | None,
    avis_median: int | None,
) -> tuple[int, str, str]:
    """
    Score le marche Amazon et retourne (score 0-10, verdict, raison).

    Seuils :
    - MICRO (0-2)   : < 10 resultats — trop micro pour dropshipping/affiliation
    - NICHE (3-5)   : 10-100 resultats — marche cible, potentiel affiliation
    - ETABLI (6-8)  : 100-1000 resultats — marche valide, bonne base produits
    - SATURE (9-10)  : > 1000 resultats — forte concurrence
    """
    if total < 10:
        score = max(0, total // 5)
        verdict = "MICRO"
        raison = (
            f"{total} produit{'s' if total > 1 else ''} sur Amazon FR — "
            "niche trop micro pour affiliation/dropshipping."
        )
    elif total < 100:
        score = 3 + min(2, total // 30)
        verdict = "NICHE"
        raison = (
            f"{total} produits sur Amazon FR — "
            "marche cible avec potentiel affiliation."
        )
    elif total < 1000:
        score = 6 + min(2, total // 300)
        verdict = "ETABLI"
        raison = (
            f"{total} produits sur Amazon FR — "
            "marche valide avec bonne base produits."
        )
    else:
        score = 9 + (1 if total > 5000 else 0)
        verdict = "SATURE"
        raison = (
            f"{total}+ produits sur Amazon FR — "
            "forte concurrence, differenciation necessaire."
        )

    extras = []
    if prix_moyen is not None:
        extras.append(f"prix moyen {prix_moyen:.2f}€")
    if avis_median is not None:
        extras.append(f"avis median {avis_median}")
    if extras:
        raison += f" ({', '.join(extras)})"

    return score, verdict, raison


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser_amazon(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse le volume de produits Amazon FR pour un mot-cle.

    Args:
        mot_cle: mot-cle a analyser
        session: session SQLAlchemy async pour le cache (optionnelle)

    Returns:
        dict avec les cles :
        - mot_cle, score_amazon (0-10), verdict (MICRO/NICHE/ETABLI/SATURE),
          verdict_raison, total_resultats, prix_moyen, avis_median, top_produits

    Fallback neutre (MICRO, score 0) si erreur.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ---------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"Amazon Market : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- Appel SerpAPI engine=amazon -----------------------------------------
    try:
        amazon_data = await _fetcher_amazon(mot_cle)
    except RuntimeError as e:
        logger.warning(f"Amazon Market : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"Amazon Market : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, f"erreur: {str(e)[:100]}")

    total = amazon_data.get("total_resultats", 0)
    items = amazon_data.get("items", [])

    # --- Prix moyen top 5 ----------------------------------------------------
    prix_top5 = [
        it["prix"] for it in items[:5]
        if it.get("prix") is not None and it["prix"] > 0
    ]
    prix_moyen = round(sum(prix_top5) / len(prix_top5), 2) if prix_top5 else None

    # --- Avis median top 5 ---------------------------------------------------
    avis_top5 = [
        it["nb_avis"] for it in items[:5]
        if it.get("nb_avis") is not None and it["nb_avis"] >= 0
    ]
    avis_median = int(statistics.median(avis_top5)) if avis_top5 else None

    # --- Top 3 produits ------------------------------------------------------
    top_produits = []
    for it in items[:3]:
        top_produits.append({
            "asin": it.get("asin", ""),
            "titre": it.get("titre", "")[:150],
            "prix": it.get("prix"),
            "nb_avis": it.get("nb_avis"),
            "note": it.get("note"),
            "url": it.get("url", ""),
        })

    # --- Scoring -------------------------------------------------------------
    score, verdict, raison = _scorer_marche(total, prix_moyen, avis_median)

    resultat = {
        "mot_cle": mot_cle,
        "score_amazon": score,
        "verdict": verdict,
        "verdict_raison": raison,
        "total_resultats": total,
        "prix_moyen": prix_moyen,
        "avis_median": avis_median,
        "top_produits": top_produits,
    }

    logger.info(
        f"Amazon Market : '{mot_cle[:30]}' -> {verdict} {score}/10 "
        f"({total} produits, prix moy={prix_moyen})"
    )

    # --- Ecriture cache 30 jours ---------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
