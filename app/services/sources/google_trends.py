"""Source de signaux : Google Trends via pytrends."""

import logging
from datetime import datetime, timezone

from pytrends.request import TrendReq

logger = logging.getLogger(__name__)


async def fetch_google_trends(mot_cle: str) -> dict:
    """
    Interroge Google Trends pour un mot-clé donné.
    Retourne les données brutes + un score partiel (0-100).
    """
    try:
        pytrends = TrendReq(hl="fr-FR", tz=60)
        pytrends.build_payload([mot_cle], timeframe="today 12-m", geo="FR")

        # Intérêt au fil du temps (12 derniers mois)
        interest_over_time = pytrends.interest_over_time()

        if interest_over_time.empty:
            return {
                "donnees": {
                    "erreur": "Aucune donnée disponible pour ce mot-clé",
                    "mot_cle": mot_cle,
                },
                "score_partiel": 0,
            }

        # Extraction des valeurs de tendance
        values = interest_over_time[mot_cle].tolist()
        dates = [d.strftime("%Y-%m-%d") for d in interest_over_time.index]

        # Calcul du score basé sur la tendance
        moyenne = sum(values) / len(values) if values else 0
        derniere_valeur = values[-1] if values else 0
        pic = max(values) if values else 0

        # Score : combinaison de la moyenne récente et de la tendance
        # - Moyenne générale contribue à 40%
        # - Dernière valeur contribue à 40%
        # - Tendance haussière bonus 20%
        score_moyenne = int(moyenne)
        score_recent = int(derniere_valeur)

        # Tendance : comparer dernier quart vs premier quart
        quart = len(values) // 4 if len(values) >= 4 else 1
        debut = sum(values[:quart]) / quart
        fin = sum(values[-quart:]) / quart
        tendance_hausse = min(20, max(0, int((fin - debut) / max(debut, 1) * 50)))

        score_partiel = min(100, int(score_moyenne * 0.4 + score_recent * 0.4 + tendance_hausse))

        # Requêtes associées (pour enrichir l'analyse)
        try:
            related_queries = pytrends.related_queries()
            top_queries = []
            if mot_cle in related_queries and related_queries[mot_cle]["top"] is not None:
                top_df = related_queries[mot_cle]["top"].head(5)
                top_queries = top_df.to_dict("records")
        except Exception:
            top_queries = []

        return {
            "donnees": {
                "mot_cle": mot_cle,
                "periode": "12 derniers mois",
                "geo": "FR",
                "moyenne": round(moyenne, 1),
                "derniere_valeur": derniere_valeur,
                "pic": pic,
                "tendance": "hausse" if fin > debut else "baisse" if fin < debut else "stable",
                "variation_pct": round((fin - debut) / max(debut, 1) * 100, 1),
                "series": dict(zip(dates, values)),
                "requetes_associees": top_queries,
                "collecte": datetime.now(timezone.utc).isoformat(),
            },
            "score_partiel": score_partiel,
        }

    except Exception as e:
        logger.error(f"Erreur Google Trends pour '{mot_cle}': {e}")
        return {
            "donnees": {
                "erreur": str(e),
                "mot_cle": mot_cle,
            },
            "score_partiel": 0,
        }
