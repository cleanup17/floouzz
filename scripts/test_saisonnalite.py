"""
Dry-run du detecteur de saisonnalite.

Flux :
1. SerpAPI engine=google_trends : recupere la serie temporelle hebdomadaire
   sur les 12 derniers mois pour un mot-cle donne (FR)
2. Calculs statistiques purs (moyenne, mediane, ecart-type, ratio pic/moyenne,
   concentration top N semaines)
3. Detection du verdict via regles deterministes :
   STABLE / CYCLIQUE / SAISONNIER / PIC_UNIQUE / AUCUNE_DONNEE
4. Detection de la phase actuelle (creux / hausse / pic / descente)
5. Rapport texte detaille avec sparkline ASCII pour visualisation

But : valider les seuils de scoring sur 4 mots-cles reels avant de coder
saisonnalite.py. Zero Claude (calculs purs), zero BDD, zero cache.
Chaque run = 1 appel SerpAPI (~0.002$).

Usage :
    docker compose exec app python -m scripts.test_saisonnalite "mot cle"
    docker compose exec app python -m scripts.test_saisonnalite   (defaut)
"""

import asyncio
import re
import statistics
import sys
from datetime import datetime
from typing import Any

import httpx

from app.config import settings

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# Mot-cle par defaut : PIC_UNIQUE attendu
MOT_CLE_DEFAUT = "chocolat paques"

# Seuils de detection — a tester et ajuster sur les 4 mots-cles
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
        "ratio_pic_moyenne_max": 8.0,
        "concentration_top8_min": 0.60,
    },
    "PIC_UNIQUE": {
        "ratio_pic_moyenne_min": 8.0,
        "concentration_top8_min": 0.80,
    },
}

# Nombre de semaines pour le calcul de concentration
CONCENTRATION_WINDOW = 8

# Scores par verdict (plage affichee)
SCORE_PAR_VERDICT = {
    "AUCUNE_DONNEE": 0,
    "STABLE": 1,
    "CYCLIQUE": 4,
    "SAISONNIER": 7,
    "PIC_UNIQUE": 10,
}

# Mapping mois FR abrege (depuis les dates SerpAPI) vers nom complet
MOIS_FR = {
    "janv.": "janvier", "févr.": "fevrier", "mars": "mars", "avr.": "avril",
    "mai": "mai", "juin": "juin", "juil.": "juillet", "août": "aout",
    "sept.": "septembre", "oct.": "octobre", "nov.": "novembre", "déc.": "decembre",
}
NOMS_MOIS = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]


# ---------------------------------------------------------------------------
# 1. Appel SerpAPI
# ---------------------------------------------------------------------------

async def fetcher_timeseries(mot_cle: str) -> tuple[list[tuple[str, int]], dict]:
    """
    Appelle SerpAPI google_trends et retourne la serie temporelle hebdomadaire.

    Retourne : (liste de (date_str, valeur), donnees_brutes_completes)
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree dans .env")

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_trends",
        "q": mot_cle,
        "geo": "FR",
        "hl": "fr",
        "date": "today 12-m",
        "data_type": "TIMESERIES",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(SERPAPI_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    timeline = data.get("interest_over_time", {}).get("timeline_data", [])
    if not timeline:
        return [], data

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

    return serie, data


# ---------------------------------------------------------------------------
# 2. Stats pures
# ---------------------------------------------------------------------------

def calculer_stats(valeurs: list[int]) -> dict[str, Any]:
    """Calcule les stats descriptives de la serie."""
    if not valeurs:
        return {
            "nb_points": 0,
            "min": 0, "max": 0,
            "moyenne": 0.0, "mediane": 0.0,
            "ecart_type": 0.0,
            "ratio_pic_moyenne": 0.0,
            "coefficient_variation": 0.0,
        }

    vmax = max(valeurs)
    vmin = min(valeurs)
    moyenne = sum(valeurs) / len(valeurs)
    mediane = statistics.median(valeurs)
    ecart_type = statistics.stdev(valeurs) if len(valeurs) > 1 else 0.0

    # Protection division par zero : si moyenne = 0, la serie est entierement plate
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


def calculer_concentration_top_n(valeurs: list[int], n: int = CONCENTRATION_WINDOW) -> float:
    """
    Retourne le % du signal total concentre sur les N valeurs les plus elevees.
    Ex: si les 8 plus grandes semaines cumulent 80% du signal annuel -> 0.80
    """
    if not valeurs:
        return 0.0
    total = sum(valeurs)
    if total == 0:
        return 0.0
    top_n = sorted(valeurs, reverse=True)[:n]
    return round(sum(top_n) / total, 3)


# ---------------------------------------------------------------------------
# 3. Detection du verdict
# ---------------------------------------------------------------------------

def detecter_verdict(stats: dict, concentration_top8: float) -> tuple[str, int, str]:
    """
    Applique les seuils et retourne (verdict, score, raison).

    Ordre d'evaluation : du plus restrictif au plus large.
    """
    if stats["nb_points"] == 0 or stats["max"] == 0:
        return "AUCUNE_DONNEE", 0, "Aucune donnee Google Trends disponible."

    ratio = stats["ratio_pic_moyenne"]
    coef = stats["coefficient_variation"]

    # PIC_UNIQUE : pic extreme + concentration tres forte
    if (ratio >= SEUILS["PIC_UNIQUE"]["ratio_pic_moyenne_min"]
            and concentration_top8 >= SEUILS["PIC_UNIQUE"]["concentration_top8_min"]):
        return (
            "PIC_UNIQUE",
            SCORE_PAR_VERDICT["PIC_UNIQUE"],
            f"Pic extreme : ratio pic/moyenne = {ratio}, "
            f"{int(concentration_top8 * 100)}% du signal sur 8 semaines.",
        )

    # SAISONNIER : ratio fort + concentration notable
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

    # CYCLIQUE : ratio modere
    if ratio >= SEUILS["STABLE"]["ratio_pic_moyenne_max"]:  # >= 2.0
        return (
            "CYCLIQUE",
            SCORE_PAR_VERDICT["CYCLIQUE"],
            f"Cycles moderes : ratio pic/moyenne = {ratio}, "
            f"coef variation = {coef}.",
        )

    # STABLE : courbe plate
    return (
        "STABLE",
        SCORE_PAR_VERDICT["STABLE"],
        f"Demande stable : ratio pic/moyenne = {ratio}, "
        f"coef variation = {coef}.",
    )


# ---------------------------------------------------------------------------
# 4. Detection de la phase actuelle
# ---------------------------------------------------------------------------

def detecter_phase(valeurs: list[int], vmax: int) -> str:
    """
    Analyse les dernieres semaines pour determiner la phase actuelle.

    Regle :
    - Si valeur actuelle >= 70% du max historique -> 'pic'
    - Si fin/avant > 1.5 -> 'hausse'
    - Si fin/avant < 0.5 -> 'descente'
    - Sinon -> 'creux'
    """
    if not valeurs or len(valeurs) < 12:
        return "creux"

    fin = valeurs[-4:]
    avant = valeurs[-12:-4]
    moyenne_fin = sum(fin) / len(fin)
    moyenne_avant = sum(avant) / len(avant) if avant else 0

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
# 5. Detection du mois du pic
# ---------------------------------------------------------------------------

def _extraire_mois_depuis_date(date_str: str) -> str | None:
    """
    Parse une date SerpAPI FR ('29 mars – 4 avr. 2026' ou '13–19 avr. 2025')
    pour extraire le mois du pic en nom FR.
    """
    if not date_str:
        return None
    date_lower = date_str.lower()
    for abrev, nom_complet in MOIS_FR.items():
        if abrev in date_lower:
            return nom_complet
    return None


def detecter_mois_pic(serie: list[tuple[str, int]]) -> tuple[str | None, str | None]:
    """
    Retourne (mois_nom, date_pic_str) de la semaine qui contient le max.
    """
    if not serie:
        return None, None
    date_max, val_max = max(serie, key=lambda x: x[1])
    mois = _extraire_mois_depuis_date(date_max)
    return mois, date_max


# ---------------------------------------------------------------------------
# 6. Sparkline ASCII
# ---------------------------------------------------------------------------

SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def sparkline(valeurs: list[int]) -> str:
    """Mini graphique ASCII compact (1 caractere = 1 semaine)."""
    if not valeurs:
        return ""
    vmax = max(valeurs)
    if vmax == 0:
        return SPARK_CHARS[0] * len(valeurs)
    # Indice 1..8 (on reserve 0 pour "valeur = 0")
    levels = len(SPARK_CHARS) - 1
    return "".join(
        SPARK_CHARS[0] if v == 0 else SPARK_CHARS[max(1, min(levels, int(v / vmax * levels)))]
        for v in valeurs
    )


# ---------------------------------------------------------------------------
# 7. Rendu rapport
# ---------------------------------------------------------------------------

def rendre_rapport(
    mot_cle: str,
    serie: list[tuple[str, int]],
    stats: dict,
    concentration_top8: float,
    verdict: str,
    score: int,
    raison: str,
    phase: str,
    mois_pic: str | None,
    date_pic: str | None,
) -> None:
    print("=" * 76)
    print("Saisonnalite — dry-run (SerpAPI Google Trends, calculs purs)")
    print("=" * 76)
    print(f"Mot-cle : '{mot_cle}'")
    print(f"Periode : 12 derniers mois (FR, granularite hebdomadaire)")
    print()

    if not serie:
        print("AUCUNE DONNEE")
        return

    print(f"Verdict : {score}/10 — [{verdict}]")
    print(f"Raison  : {raison}")
    print()

    # Stats
    print("-" * 76)
    print("STATS")
    print("-" * 76)
    for k, v in stats.items():
        print(f"  {k:<25} {v}")
    print(f"  {'concentration_top8':<25} {concentration_top8}  ({int(concentration_top8 * 100)}%)")
    print()

    # Pic et phase
    print("-" * 76)
    print("PIC ET PHASE")
    print("-" * 76)
    print(f"  Mois du pic     : {mois_pic or '?'}")
    print(f"  Date du pic     : {date_pic or '?'}")
    print(f"  Phase actuelle  : {phase}")
    print()

    # Sparkline
    valeurs = [v for _, v in serie]
    spark = sparkline(valeurs)
    print("-" * 76)
    print("SPARKLINE (1 caractere = 1 semaine, du plus ancien au plus recent)")
    print("-" * 76)
    print(f"  {spark}")
    print(f"  min={min(valeurs)}  max={max(valeurs)}")
    print()

    # Top 8 semaines (pour valider la detection)
    print("-" * 76)
    print("TOP 8 SEMAINES LES PLUS ELEVEES")
    print("-" * 76)
    top8 = sorted(serie, key=lambda x: x[1], reverse=True)[:8]
    for date, val in top8:
        barre = "█" * int(val / 5)
        print(f"  {val:>4}  {date:<35} {barre}")
    print()

    # Bas 8 semaines (pour valider les creux)
    print("-" * 76)
    print("BAS 8 SEMAINES LES PLUS FAIBLES")
    print("-" * 76)
    bas8 = sorted(serie, key=lambda x: x[1])[:8]
    for date, val in bas8:
        barre = "█" * int(val / 5) if val > 0 else ""
        print(f"  {val:>4}  {date:<35} {barre}")
    print()
    print("Dry-run termine. Aucune donnee persistee.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    mot_cle = sys.argv[1] if len(sys.argv) > 1 else MOT_CLE_DEFAUT

    try:
        serie, data_brute = await fetcher_timeseries(mot_cle)
    except RuntimeError as e:
        print(f"ERREUR : {e}")
        return
    except Exception as e:
        print(f"ERREUR SerpAPI : {e}")
        return

    valeurs = [v for _, v in serie]
    stats = calculer_stats(valeurs)
    concentration_top8 = calculer_concentration_top_n(valeurs, CONCENTRATION_WINDOW)
    verdict, score, raison = detecter_verdict(stats, concentration_top8)
    phase = detecter_phase(valeurs, stats["max"])
    mois_pic, date_pic = detecter_mois_pic(serie)

    rendre_rapport(
        mot_cle=mot_cle,
        serie=serie,
        stats=stats,
        concentration_top8=concentration_top8,
        verdict=verdict,
        score=score,
        raison=raison,
        phase=phase,
        mois_pic=mois_pic,
        date_pic=date_pic,
    )


if __name__ == "__main__":
    asyncio.run(main())
