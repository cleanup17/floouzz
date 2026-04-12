"""
Service Marketplace Gap Detector — detection de vendeurs actifs sur les
marketplaces FR (Etsy, Rakuten, eBay) via footprints Google.

Pour un mot-cle donne :
1. Lance 3 requetes SerpAPI paralleles avec des footprints `site:` cibles
   sur chaque marketplace pour compter les vendeurs/produits indexes
2. Pour chaque plateforme, recupere :
   - total_results (estimation Google du nombre de resultats)
   - organic_results (echantillon visible des 10 premiers)
3. Score mathematique pur (sans Claude) base sur le total cumule et le
   nombre de plateformes actives
4. Verdict deterministe : AUCUN / FAIBLE / MOYEN / SATURE
5. Recommandations actionnables selon le verdict
6. Cache 30 jours dans cache_ia (source='marketplace_gap')

Pas d'appel Claude : compter des resultats marketplace est un probleme
de comptage, pas de jugement. Plus rapide et determinist (~0.006$ par
analyse, 3 SerpAPI seulement).

Footprints utilises (extension future possible : Vinted, Leboncoin) :
  - site:etsy.com/fr/listing "{mot_cle}"
  - site:fr.shopping.rakuten.com "{mot_cle}"
  - site:www.ebay.fr "{mot_cle}"

Fallbacks : pas de cle API, erreur SerpAPI -> resultat neutre par defaut
(verdict AUCUN, score 0) pour ne jamais crasher l'appelant.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# Cache 30 jours : les vendeurs marketplaces bougent lentement, pas la
# peine de re-spammer SerpAPI sur le meme mot-cle. Aligne sur le TTL
# d'affiliate_finder et saisonnalite.
CACHE_TTL_HEURES = 24 * 30

CACHE_SOURCE = "marketplace_gap"

# Nombre de resultats demandes par requete SerpAPI (suffisant pour
# l'echantillon visible — le total_results est independant)
SERPAPI_NUM_RESULTS = 10

# Plateformes ciblees : nom affiche + footprint Google site:
# L'ordre est preserve dans le rapport final
PLATEFORMES = [
    {
        "nom": "Etsy",
        "domaine": "etsy.com/fr/listing",
        "footprint": 'site:etsy.com/fr/listing "{mot_cle}"',
    },
    {
        "nom": "Rakuten",
        "domaine": "fr.shopping.rakuten.com",
        "footprint": 'site:fr.shopping.rakuten.com "{mot_cle}"',
    },
    {
        "nom": "eBay",
        "domaine": "www.ebay.fr",
        "footprint": 'site:www.ebay.fr "{mot_cle}"',
    },
]

VERDICTS_VALIDES = {"AUCUN", "FAIBLE", "MOYEN", "SATURE"}


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec ou donnees insuffisantes."""
    return {
        "mot_cle": mot_cle,
        "score_marketplace": 0,
        "verdict": "AUCUN",
        "verdict_raison": f"Analyse marketplace non disponible ({raison}).",
        "plateformes_actives": 0,
        "total_resultats": 0,
        "details_par_plateforme": [],
        "recommandations": [],
        "requetes_utilisees": [],
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    """
    Hash stable du mot-cle normalise.
    Prefixe 'marketplace_gap:' pour zero collision avec les autres services.
    """
    payload = f"marketplace_gap:{mot_cle.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lire_cache(
    session: AsyncSession, hash_contenu: str,
) -> dict[str, Any] | None:
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
        logger.debug(f"Marketplace Gap : cache indisponible (lecture) — {e}")
        return None


async def _ecrire_cache(
    session: AsyncSession,
    hash_contenu: str,
    resultat: dict[str, Any],
) -> None:
    """Ecrit un resultat en cache avec TTL 30 jours."""
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
        logger.debug(f"Marketplace Gap : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Appels SerpAPI paralleles
# ---------------------------------------------------------------------------

async def _fetcher_une_plateforme(
    client: httpx.AsyncClient,
    plateforme: dict,
    mot_cle: str,
) -> dict[str, Any]:
    """
    Execute une requete SerpAPI pour une plateforme et retourne :
      - nom : nom affiche de la plateforme
      - domaine : domaine de la plateforme
      - footprint : la requete reellement envoyee
      - total_resultats : estimation Google (search_information.total_results)
      - echantillon_visible : nb d'organic_results retournes (max 10)
      - exemples : 3 premiers resultats {titre, url}
      - erreur : str si la requete a echoue, sinon None
    """
    requete = plateforme["footprint"].format(mot_cle=mot_cle)

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": requete,
        "gl": "fr",
        "hl": "fr",
        "num": SERPAPI_NUM_RESULTS,
    }

    base = {
        "nom": plateforme["nom"],
        "domaine": plateforme["domaine"],
        "footprint": requete,
        "total_resultats": 0,
        "echantillon_visible": 0,
        "exemples": [],
        "erreur": None,
    }

    try:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        base["erreur"] = str(e)[:200]
        return base

    # Total estime par Google (peut etre tres approximatif mais c'est ce qu'on a)
    search_info = data.get("search_information") or {}
    total = search_info.get("total_results", 0)
    try:
        base["total_resultats"] = int(total) if total else 0
    except (ValueError, TypeError):
        base["total_resultats"] = 0

    # Echantillon visible : organic_results retournes
    organic = data.get("organic_results") or []
    base["echantillon_visible"] = len(organic)

    # 3 premiers resultats pour donner du contexte (titre + url)
    exemples = []
    for item in organic[:3]:
        titre = (item.get("title") or "").strip()[:150]
        url = (item.get("link") or "").strip()
        if titre and url:
            exemples.append({"titre": titre, "url": url})
    base["exemples"] = exemples

    return base


async def _fetcher_toutes_plateformes(
    mot_cle: str,
) -> tuple[list[dict], list[str]]:
    """
    Lance les 3 requetes SerpAPI en parallele.

    Retourne (liste des dicts par plateforme, liste des footprints utilises).
    Leve RuntimeError si SERPAPI_KEY absente.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

    async with httpx.AsyncClient(timeout=30) as client:
        details = await asyncio.gather(
            *(
                _fetcher_une_plateforme(client, p, mot_cle)
                for p in PLATEFORMES
            ),
            return_exceptions=False,  # _fetcher_une_plateforme ne raise jamais
        )

    requetes = [d["footprint"] for d in details]
    return list(details), requetes


# ---------------------------------------------------------------------------
# Detection du verdict via regles deterministes
# ---------------------------------------------------------------------------

def _detecter_verdict(
    total: int, nb_actives: int,
) -> tuple[str, int, str]:
    """
    Applique les seuils mathematiques et retourne (verdict, score, raison).

    Regles :
    - AUCUN : 0 plateforme active OU total = 0
    - FAIBLE : < 50 resultats cumules (marche emergent / peu de vendeurs)
    - MOYEN : 50-500 resultats (marche etabli sans saturation)
    - SATURE : > 500 resultats (marche mature, forte concurrence)

    Le score 0-10 est lineaire et reflete la "presence sur marketplaces".
    L'interpretation business (opportunite vs concurrence) est laissee a
    l'utilisatrice via les recommandations textuelles.
    """
    if nb_actives == 0 or total == 0:
        return (
            "AUCUN",
            0,
            "Aucun vendeur detecte sur les marketplaces FR ciblees "
            "(Etsy, Rakuten, eBay).",
        )

    if total < 50:
        # Score 2-4 selon le nombre de plateformes actives
        score = max(2, 1 + nb_actives)
        return (
            "FAIBLE",
            min(4, score),
            f"{total} resultats sur {nb_actives} plateforme(s) — "
            "marche emergent ou peu de vendeurs visibles.",
        )

    if total < 500:
        # Score 5-7 selon le nombre de plateformes actives
        score = 4 + nb_actives
        return (
            "MOYEN",
            min(7, score),
            f"{total} resultats sur {nb_actives} plateforme(s) — "
            "marche etabli sans saturation.",
        )

    # Total >= 500 : marche sature
    score = min(10, 7 + nb_actives)
    return (
        "SATURE",
        score,
        f"{total}+ resultats sur {nb_actives} plateforme(s) — "
        "marche mature avec forte concurrence.",
    )


# ---------------------------------------------------------------------------
# Generation des recommandations actionnables
# ---------------------------------------------------------------------------

def _generer_recommandations(
    verdict: str,
    nb_actives: int,
    details: list[dict],
) -> list[str]:
    """
    Genere 1-3 recommandations actionnables selon le verdict.

    Les recommandations sont pensees pour une entrepreneuse solo qui se
    demande "est-ce qu'il y a un marche pour mon produit ?".
    """
    recos: list[str] = []

    if verdict == "AUCUN":
        recos.append(
            "Verifier que le produit/service correspond a une demande reelle. "
            "Risque de niche inexistante ou hors-marketplace."
        )
        recos.append(
            "Si c'est un service / produit numerique, les marketplaces grand "
            "public ne sont pas pertinentes — ce n'est pas un mauvais signal."
        )
        return recos

    if verdict == "FAIBLE":
        recos.append(
            "Marche emergent — opportunite d'arriver tot. Tester avec un MVP "
            "avant de scaler."
        )
        if nb_actives == 1:
            actives_noms = [d["nom"] for d in details if d.get("total_resultats", 0) > 0]
            if actives_noms:
                recos.append(
                    f"Une seule plateforme active ({actives_noms[0]}) — "
                    "explorer les autres pour valider l'absence de demande."
                )
        return recos

    if verdict == "MOYEN":
        recos.append(
            "Marche valide sans saturation excessive — sweet spot pour se "
            "positionner avec une differenciation claire."
        )
        recos.append(
            "Differenciation possible via niche specifique, qualite premium, "
            "ou service apres-vente."
        )
        # Identifier la plateforme dominante
        if details:
            tries = sorted(
                [d for d in details if d.get("total_resultats", 0) > 0],
                key=lambda x: x.get("total_resultats", 0),
                reverse=True,
            )
            if tries:
                recos.append(
                    f"Plateforme dominante : {tries[0]['nom']} — commencer "
                    "par celle-ci pour un test rapide."
                )
        return recos

    # SATURE
    recos.append(
        "Forte concurrence — chercher une sous-niche specifique ou une "
        "proposition de valeur unique."
    )
    recos.append(
        "Eviter les marketplaces grand public en direct — privilegier "
        "le contenu/affiliation ou un canal proprietaire."
    )
    recos.append(
        "Si tu vises quand meme les marketplaces : differenciation forte "
        "obligatoire (qualite, branding, photos premium, packaging)."
    )
    return recos


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser_marketplace(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse la presence de vendeurs sur les marketplaces FR (Etsy, Rakuten,
    eBay) pour un mot-cle donne.

    Args:
        mot_cle: mot-cle a analyser (normalise pour le cache)
        session: session SQLAlchemy async pour le cache (optionnelle).
            Si fournie, lit/ecrit dans cache_ia avec TTL 30 jours.

    Returns:
        dict avec les cles :
        - mot_cle : str
        - score_marketplace : int (0-10)
        - verdict : str (AUCUN / FAIBLE / MOYEN / SATURE)
        - verdict_raison : str
        - plateformes_actives : int (0-3)
        - total_resultats : int (cumule sur les 3 plateformes)
        - details_par_plateforme : list[dict] (nom, domaine, total_resultats,
                                                echantillon_visible, exemples)
        - recommandations : list[str] (1-3 pistes actionnables)
        - requetes_utilisees : list[str] (3 footprints)

    Tous les chemins d'erreur retournent un resultat par defaut neutre
    (verdict AUCUN, score 0) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ---------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"Marketplace Gap : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- 3 requetes SerpAPI paralleles ---------------------------------------
    try:
        details, requetes = await _fetcher_toutes_plateformes(mot_cle)
    except RuntimeError as e:
        logger.warning(f"Marketplace Gap : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"Marketplace Gap : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_erreur")

    # --- Calculs deterministes -----------------------------------------------
    total_resultats = sum(d.get("total_resultats", 0) for d in details)
    plateformes_actives = sum(
        1 for d in details
        if d.get("total_resultats", 0) > 0 and not d.get("erreur")
    )

    verdict, score, raison = _detecter_verdict(total_resultats, plateformes_actives)
    recommandations = _generer_recommandations(verdict, plateformes_actives, details)

    resultat = {
        "mot_cle": mot_cle,
        "score_marketplace": score,
        "verdict": verdict,
        "verdict_raison": raison,
        "plateformes_actives": plateformes_actives,
        "total_resultats": total_resultats,
        "details_par_plateforme": details,
        "recommandations": recommandations,
        "requetes_utilisees": requetes,
    }

    logger.info(
        f"Marketplace Gap : '{mot_cle[:40]}' -> {verdict} {score}/10 "
        f"({plateformes_actives}/{len(PLATEFORMES)} plateformes, "
        f"{total_resultats} resultats)"
    )

    # --- Ecriture cache 30 jours ---------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
