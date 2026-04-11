"""
Service Detection de saisonnalite — analyse la courbe Google Trends 12 mois.

Pour un mot-cle donne :
1. Recupere la serie temporelle hebdomadaire sur les 12 derniers mois via
   SerpAPI engine=google_trends (~53 points, granularite semaine)
2. Calcule les stats descriptives (moyenne, ecart-type, ratio pic/moyenne,
   concentration du signal sur les top N semaines)
3. Applique des regles deterministes pour classifier en 5 verdicts :
   STABLE / CYCLIQUE / SAISONNIER / PIC_UNIQUE / AUCUNE_DONNEE
4. Detecte la phase actuelle (creux / hausse / pic / descente)
5. Extrait le mois du pic principal
6. Genere des recommandations actionnables
7. Cache 30 jours dans cache_ia (source='saisonnalite')

Pas d'appel Claude : les stats sont des calculs purs, Claude n'apporte
rien sur ce sujet. Plus rapide (~0.002$ par appel, 1 SerpAPI seul) et
totalement deterministe.

Fallbacks : pas de cle API, erreur SerpAPI, serie vide -> resultat neutre
(verdict AUCUNE_DONNEE, score 0) pour ne jamais crasher l'appelant.
"""

import hashlib
import json
import logging
import re
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

# Cache 30 jours : les courbes Google Trends bougent tres lentement sur 12
# mois, un pic saisonnier reste stable d'une annee sur l'autre. Aligne sur
# le TTL d'affiliate_finder (meme horizon de fraicheur).
CACHE_TTL_HEURES = 24 * 30

CACHE_SOURCE = "saisonnalite"

# Fenetre temporelle : 12 mois hebdomadaires (~53 points)
DATE_RANGE = "today 12-m"

# Nombre de semaines pour le calcul de concentration (top N semaines / total)
CONCENTRATION_WINDOW = 8

# Verdicts possibles
VERDICTS_VALIDES = {
    "AUCUNE_DONNEE",
    "STABLE",
    "CYCLIQUE",
    "SAISONNIER",
    "PIC_UNIQUE",
}

# Seuils de detection (valides sur 4 mots-cles en dry-run)
SEUILS = {
    "STABLE": {
        "ratio_pic_moyenne_max": 2.0,
        "coef_variation_max": 0.4,
    },
    "CYCLIQUE": {
        "ratio_pic_moyenne_max": 4.0,
        "coef_variation_max": 0.8,
    },
    "SAISONNIER": {
        "concentration_top8_min": 0.60,
    },
    "PIC_UNIQUE": {
        "ratio_pic_moyenne_min": 8.0,
        "concentration_top8_min": 0.80,
    },
}

# Scores associes aux verdicts (plage centrale affichee dans la fiche)
SCORE_PAR_VERDICT = {
    "AUCUNE_DONNEE": 0,
    "STABLE": 1,
    "CYCLIQUE": 4,
    "SAISONNIER": 7,
    "PIC_UNIQUE": 10,
}

# Mapping mois FR abrege (format SerpAPI) -> nom complet sans accent
MOIS_FR_ABREV_VERS_NOM = {
    "janv.": "janvier",
    "févr.": "fevrier",
    "mars": "mars",
    "avr.": "avril",
    "mai": "mai",
    "juin": "juin",
    "juil.": "juillet",
    "août": "aout",
    "sept.": "septembre",
    "oct.": "octobre",
    "nov.": "novembre",
    "déc.": "decembre",
}

# Liste ordonnee des mois pour calculer la distance au pic
NOMS_MOIS = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(mot_cle: str, raison: str) -> dict[str, Any]:
    """Resultat neutre en cas d'echec ou donnees insuffisantes."""
    return {
        "mot_cle": mot_cle,
        "periode": "12 derniers mois",
        "geo": "FR",
        "score_saisonnalite": 0,
        "verdict": "AUCUNE_DONNEE",
        "verdict_raison": f"Analyse saisonnalite non disponible ({raison}).",
        "stats": {},
        "pic": {},
        "position_actuelle": {},
        "recommandations": [],
        "serie": {},
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    """
    Hash stable du mot-cle normalise.
    Prefixe 'saisonnalite:' pour zero collision avec les autres services
    (pipeline_ia, serp_gap, affiliate_finder).
    """
    payload = f"saisonnalite:{mot_cle.strip().lower()}"
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
        logger.debug(f"Saisonnalite : cache indisponible (lecture) — {e}")
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
        logger.debug(f"Saisonnalite : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Appel SerpAPI
# ---------------------------------------------------------------------------

async def _fetcher_timeseries(mot_cle: str) -> list[tuple[str, int]]:
    """
    Appelle SerpAPI google_trends et retourne la serie temporelle
    hebdomadaire sur 12 mois : liste de (date_str, valeur_0_100).

    Leve RuntimeError si SERPAPI_KEY absente, Exception si erreur reseau.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_trends",
        "q": mot_cle,
        "geo": "FR",
        "hl": "fr",
        "date": DATE_RANGE,
        "data_type": "TIMESERIES",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    timeline = data.get("interest_over_time", {}).get("timeline_data", [])
    if not timeline:
        return []

    serie: list[tuple[str, int]] = []
    for point in timeline:
        date = point.get("date", "")
        valeurs = point.get("values", [])
        if not valeurs:
            continue
        val = valeurs[0].get("extracted_value", 0) or 0
        try:
            val_int = int(val)
        except (ValueError, TypeError):
            val_int = 0
        serie.append((date, val_int))

    return serie


# ---------------------------------------------------------------------------
# Calculs statistiques purs
# ---------------------------------------------------------------------------

def _calculer_stats(valeurs: list[int]) -> dict[str, Any]:
    """Calcule les stats descriptives de la serie temporelle."""
    if not valeurs:
        return {
            "nb_points": 0,
            "min": 0,
            "max": 0,
            "moyenne": 0.0,
            "mediane": 0.0,
            "ecart_type": 0.0,
            "ratio_pic_moyenne": 0.0,
            "coefficient_variation": 0.0,
        }

    vmax = max(valeurs)
    vmin = min(valeurs)
    moyenne = sum(valeurs) / len(valeurs)
    mediane = statistics.median(valeurs)
    ecart_type = statistics.stdev(valeurs) if len(valeurs) > 1 else 0.0

    # Protection division par zero : si moyenne = 0, la serie est entierement
    # plate a zero -> pas de signal exploitable
    ratio_pic = (vmax / moyenne) if moyenne > 0 else 0.0
    coef_var = (ecart_type / moyenne) if moyenne > 0 else 0.0

    return {
        "nb_points": len(valeurs),
        "min": vmin,
        "max": vmax,
        "moyenne": round(moyenne, 2),
        "mediane": round(mediane, 2),
        "ecart_type": round(ecart_type, 2),
        "ratio_pic_moyenne": round(ratio_pic, 2),
        "coefficient_variation": round(coef_var, 2),
    }


def _calculer_concentration_top_n(
    valeurs: list[int], n: int = CONCENTRATION_WINDOW,
) -> float:
    """
    Pourcentage du signal total concentre sur les N valeurs les plus
    elevees. Si les 8 plus grandes semaines cumulent 80% du signal annuel,
    retourne 0.80.
    """
    if not valeurs:
        return 0.0
    total = sum(valeurs)
    if total == 0:
        return 0.0
    top_n = sorted(valeurs, reverse=True)[:n]
    return round(sum(top_n) / total, 3)


# ---------------------------------------------------------------------------
# Detection du verdict via regles deterministes
# ---------------------------------------------------------------------------

def _detecter_verdict(stats: dict, concentration_top8: float) -> tuple[str, int, str]:
    """
    Applique les seuils et retourne (verdict, score, raison).

    Ordre d'evaluation : du plus restrictif au plus large.
    """
    if stats["nb_points"] == 0 or stats["max"] == 0:
        return (
            "AUCUNE_DONNEE",
            0,
            "Aucune donnee Google Trends disponible sur cette periode.",
        )

    ratio = stats["ratio_pic_moyenne"]
    coef = stats["coefficient_variation"]

    # PIC_UNIQUE : pic extreme (>=8x moyenne) + concentration >= 80%
    if (ratio >= SEUILS["PIC_UNIQUE"]["ratio_pic_moyenne_min"]
            and concentration_top8 >= SEUILS["PIC_UNIQUE"]["concentration_top8_min"]):
        return (
            "PIC_UNIQUE",
            SCORE_PAR_VERDICT["PIC_UNIQUE"],
            f"Pic saisonnier extreme : ratio pic/moyenne = {ratio}, "
            f"{int(concentration_top8 * 100)}% du signal sur 8 semaines.",
        )

    # SAISONNIER : ratio >= 4 et (coef >= 0.8 OU concentration >= 60%)
    if (ratio >= SEUILS["CYCLIQUE"]["ratio_pic_moyenne_max"]  # >= 4.0
            and (coef >= SEUILS["CYCLIQUE"]["coef_variation_max"]  # >= 0.8
                 or concentration_top8 >= SEUILS["SAISONNIER"]["concentration_top8_min"])):
        return (
            "SAISONNIER",
            SCORE_PAR_VERDICT["SAISONNIER"],
            f"Saisonnalite marquee : ratio pic/moyenne = {ratio}, "
            f"coef variation = {coef}, "
            f"{int(concentration_top8 * 100)}% sur 8 semaines.",
        )

    # CYCLIQUE : ratio entre 2 et 4
    if ratio >= SEUILS["STABLE"]["ratio_pic_moyenne_max"]:  # >= 2.0
        return (
            "CYCLIQUE",
            SCORE_PAR_VERDICT["CYCLIQUE"],
            f"Cycles moderes : ratio pic/moyenne = {ratio}, "
            f"coef variation = {coef}.",
        )

    # STABLE : ratio < 2.0 (demande constante)
    return (
        "STABLE",
        SCORE_PAR_VERDICT["STABLE"],
        f"Demande stable : ratio pic/moyenne = {ratio}, "
        f"coef variation = {coef}.",
    )


# ---------------------------------------------------------------------------
# Detection de la phase actuelle (creux / hausse / pic / descente)
# ---------------------------------------------------------------------------

def _detecter_phase(valeurs: list[int], vmax: int) -> str:
    """
    Analyse les dernieres semaines pour determiner la phase actuelle.

    Regles :
    - Si moyenne des 4 dernieres semaines >= 70% du max historique -> 'pic'
    - Si ratio fin/avant > 1.5 -> 'hausse' (on monte vers un pic)
    - Si ratio fin/avant < 0.5 -> 'descente' (on redescend apres un pic)
    - Sinon -> 'creux'
    """
    if not valeurs or len(valeurs) < 12:
        return "creux"

    fin = valeurs[-4:]
    avant = valeurs[-12:-4]
    moyenne_fin = sum(fin) / len(fin)
    moyenne_avant = sum(avant) / len(avant) if avant else 0.0

    # Phase 'pic' : on est proche du max historique
    if vmax > 0 and moyenne_fin >= vmax * 0.7:
        return "pic"

    if moyenne_avant > 0:
        ratio = moyenne_fin / moyenne_avant
        if ratio > 1.5:
            return "hausse"
        if ratio < 0.5:
            return "descente"

    return "creux"


# ---------------------------------------------------------------------------
# Extraction du mois du pic depuis la date SerpAPI
# ---------------------------------------------------------------------------

def _extraire_mois_depuis_date(date_str: str) -> str | None:
    """
    Parse une date SerpAPI FR ('29 mars – 4 avr. 2026') et retourne le
    nom complet du mois sans accent ('avril', 'decembre'...). None si
    illisible.

    On prend le premier mois trouve dans la chaine — pour les semaines
    qui chevauchent deux mois, c'est le plus ancien qui gagne. C'est
    acceptable car on cherche juste le mois dominant du pic.
    """
    if not date_str:
        return None
    date_lower = date_str.lower()

    # Recherche du premier mois trouve dans la chaine
    positions: list[tuple[int, str]] = []
    for abrev, nom in MOIS_FR_ABREV_VERS_NOM.items():
        idx = date_lower.find(abrev)
        if idx >= 0:
            positions.append((idx, nom))

    if not positions:
        return None

    # Le mois le plus a gauche dans la chaine est le mois du debut de semaine
    positions.sort()
    return positions[0][1]


def _detecter_mois_pic(serie: list[tuple[str, int]]) -> tuple[str | None, str | None, int]:
    """
    Retourne (mois_nom, date_pic_str, valeur_pic) pour la semaine qui
    contient le max. En cas d'egalite, on prend la plus recente (la plus a
    droite dans la serie) — plus pertinent pour un pic qui se repete.
    """
    if not serie:
        return None, None, 0
    # On parcourt de la fin vers le debut pour garder le dernier max en cas
    # d'egalite (pic le plus recent)
    date_max, val_max = serie[0]
    for date, val in serie:
        if val >= val_max:
            date_max, val_max = date, val
    mois = _extraire_mois_depuis_date(date_max)
    return mois, date_max, val_max


# ---------------------------------------------------------------------------
# Generation des recommandations actionnables
# ---------------------------------------------------------------------------

def _mois_actuel_fr() -> str:
    """Retourne le mois courant en FR sans accent (ex: 'avril')."""
    idx = datetime.now(timezone.utc).month - 1
    return NOMS_MOIS[idx]


def _distance_au_pic_mois(mois_actuel: str, mois_pic: str) -> int:
    """
    Distance en mois entre le mois courant et le mois du pic.
    Retourne 0 si identiques, sinon la distance signee :
      - positive si le pic est dans le futur (mois_pic vient apres)
      - negative si le pic est deja passe
    En fait on retourne la distance 'prochaine occurrence' modulo 12 :
      mois_actuel=avril, mois_pic=juin -> 2
      mois_actuel=juin, mois_pic=avril -> 10 (prochain cycle l'an prochain)
    """
    if not mois_actuel or not mois_pic:
        return 0
    if mois_actuel not in NOMS_MOIS or mois_pic not in NOMS_MOIS:
        return 0

    idx_actuel = NOMS_MOIS.index(mois_actuel)
    idx_pic = NOMS_MOIS.index(mois_pic)
    distance = (idx_pic - idx_actuel) % 12
    return distance


def _generer_recommandations(
    verdict: str,
    phase: str,
    mois_pic: str | None,
    mois_actuel: str,
    distance: int,
) -> list[str]:
    """
    Genere 1-3 recommandations actionnables selon le verdict et la phase.

    La logique est pensee pour un createur solo qui se demande
    "est-ce que je lance maintenant ou j'attends ?".
    """
    recos: list[str] = []

    # STABLE : aucune contrainte temporelle
    if verdict == "STABLE":
        recos.append("Demande constante toute l'annee — lance quand tu es pret.")
        return recos

    # AUCUNE_DONNEE : rien a dire
    if verdict == "AUCUNE_DONNEE":
        recos.append("Volume de recherche trop faible pour analyse saisonniere.")
        return recos

    # CYCLIQUE / SAISONNIER / PIC_UNIQUE : on regarde la phase
    if verdict == "PIC_UNIQUE":
        intensite = "extreme"
    elif verdict == "SAISONNIER":
        intensite = "marque"
    else:
        intensite = "modere"

    if phase == "pic":
        recos.append(
            f"Pic {intensite} en cours — probablement trop tard pour lancer "
            "ce cycle, prepare le prochain."
        )
        if mois_pic:
            recos.append(
                f"Prochain pic : {mois_pic} de l'annee prochaine — "
                "commence a preparer 2 mois avant."
            )
    elif phase == "hausse":
        recos.append("Montee vers le pic — lance MAINTENANT si ton produit est pret.")
        if mois_pic:
            recos.append(f"Pic attendu vers : {mois_pic}.")
    elif phase == "descente":
        if mois_pic:
            recos.append(
                f"Pic deja passe — prochaine fenetre en {mois_pic} "
                f"(dans ~{distance} mois)."
            )
        else:
            recos.append("Pic deja passe — attends le prochain cycle.")
        recos.append("Utilise cette periode creuse pour preparer le contenu.")
    else:  # creux
        if mois_pic and distance > 0:
            mois_avant = max(1, distance - 1)
            recos.append(
                f"Creux actuel, pic attendu en {mois_pic} (dans {distance} mois)."
            )
            recos.append(
                f"Lance ton contenu {mois_avant} mois avant pour etre indexe "
                "au bon moment."
            )
        else:
            recos.append("Hors saison — attends le prochain cycle.")

    return recos


# ---------------------------------------------------------------------------
# Construction des sous-dicts du resultat final
# ---------------------------------------------------------------------------

def _construire_pic_info(
    mois_pic: str | None,
    date_pic: str | None,
    val_pic: int,
    valeurs: list[int],
) -> dict[str, Any]:
    """Bloc 'pic' du resultat final."""
    semaines_top_80 = 0
    if valeurs and sum(valeurs) > 0:
        tries = sorted(valeurs, reverse=True)
        total = sum(valeurs)
        cumul = 0
        for i, v in enumerate(tries, 1):
            cumul += v
            if cumul / total >= 0.80:
                semaines_top_80 = i
                break

    return {
        "mois_principal": mois_pic,
        "date_pic": date_pic,
        "valeur_pic": val_pic,
        "semaines_top_80pct": semaines_top_80,
    }


def _construire_position_actuelle(
    mois_actuel: str,
    mois_pic: str | None,
    phase: str,
) -> dict[str, Any]:
    """Bloc 'position_actuelle' du resultat final."""
    distance = _distance_au_pic_mois(mois_actuel, mois_pic) if mois_pic else 0
    return {
        "mois_actuel": mois_actuel,
        "distance_au_pic_mois": distance,
        "phase": phase,
    }


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser_saisonnalite(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse la saisonnalite d'un mot-cle sur 12 mois Google Trends.

    Args:
        mot_cle: mot-cle a analyser (normalise pour le cache)
        session: session SQLAlchemy async pour le cache (optionnelle).
            Si fournie, lit/ecrit dans cache_ia avec TTL 30 jours.

    Returns:
        dict avec les cles :
        - mot_cle : str
        - periode : str ('12 derniers mois')
        - geo : str ('FR')
        - score_saisonnalite : int (0-10)
        - verdict : str (STABLE / CYCLIQUE / SAISONNIER / PIC_UNIQUE / AUCUNE_DONNEE)
        - verdict_raison : str
        - stats : dict (nb_points, min, max, moyenne, mediane, ecart_type,
                        ratio_pic_moyenne, coefficient_variation, concentration_top8)
        - pic : dict (mois_principal, date_pic, valeur_pic, semaines_top_80pct)
        - position_actuelle : dict (mois_actuel, distance_au_pic_mois, phase)
        - recommandations : list[str] (1-3 pistes actionnables)
        - serie : dict {date_str: valeur} (serie complete pour visualisation)

    Tous les chemins d'erreur retournent un resultat par defaut neutre
    (verdict AUCUNE_DONNEE, score 0) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ---------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"Saisonnalite : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- Appel SerpAPI -------------------------------------------------------
    try:
        serie = await _fetcher_timeseries(mot_cle)
    except RuntimeError as e:
        logger.warning(f"Saisonnalite : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"Saisonnalite : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_erreur")

    if not serie:
        logger.info(f"Saisonnalite : serie vide pour '{mot_cle[:40]}'")
        return _resultat_par_defaut(mot_cle, "serie_vide")

    # --- Calculs purs --------------------------------------------------------
    valeurs = [v for _, v in serie]
    stats = _calculer_stats(valeurs)
    concentration_top8 = _calculer_concentration_top_n(valeurs, CONCENTRATION_WINDOW)
    stats["concentration_top8"] = concentration_top8

    verdict, score, raison = _detecter_verdict(stats, concentration_top8)
    phase = _detecter_phase(valeurs, stats["max"])
    mois_pic, date_pic, val_pic = _detecter_mois_pic(serie)
    mois_actuel = _mois_actuel_fr()

    # --- Sous-dicts du resultat final ----------------------------------------
    pic_info = _construire_pic_info(mois_pic, date_pic, val_pic, valeurs)
    position = _construire_position_actuelle(mois_actuel, mois_pic, phase)
    distance = position["distance_au_pic_mois"]
    recommandations = _generer_recommandations(
        verdict, phase, mois_pic, mois_actuel, distance,
    )

    # Serie complete en dict pour visualisation UI
    serie_dict = {date: val for date, val in serie}

    resultat = {
        "mot_cle": mot_cle,
        "periode": "12 derniers mois",
        "geo": "FR",
        "score_saisonnalite": score,
        "verdict": verdict,
        "verdict_raison": raison,
        "stats": stats,
        "pic": pic_info,
        "position_actuelle": position,
        "recommandations": recommandations,
        "serie": serie_dict,
    }

    logger.info(
        f"Saisonnalite : '{mot_cle[:40]}' -> {verdict} {score}/10 "
        f"(pic={mois_pic}, phase={phase})"
    )

    # --- Ecriture cache 30 jours ---------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
