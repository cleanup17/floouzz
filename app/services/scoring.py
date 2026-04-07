"""Service de scoring — calcul du score global et du verdict."""


# Mapping source → dimension de score
SOURCE_TO_DIMENSION = {
    # Demande (40%)
    "google_trends": "demande",
    "google_news": "demande",
    # Douleur (20%)
    "reddit": "douleur",
    "hackernews": "douleur",
    "google_search_paa": "douleur",
    # Concurrence (20%) — score inverse : peu de concurrents = bon score
    "producthunt": "concurrence",
    "google_search_concurrence": "concurrence",
    # Monetisation (20%)
    "google_jobs": "monetisation",
    "google_search_ads": "monetisation",
}


def calculer_score_global(signaux: list[dict]) -> dict:
    """
    Calcule le score global a partir des signaux collectes.
    Chaque signal alimente une dimension (demande, douleur, concurrence, monetisation).
    Si plusieurs signaux alimentent la meme dimension, on fait la moyenne.
    Les dimensions sans signal restent a 50 (neutre).
    """
    dimensions: dict[str, list[int]] = {
        "demande": [],
        "douleur": [],
        "concurrence": [],
        "monetisation": [],
    }

    for signal in signaux:
        source = signal.get("source", "")
        dimension = SOURCE_TO_DIMENSION.get(source)
        if dimension:
            dimensions[dimension].append(signal.get("score_partiel", 0))

    score_demande = _moyenne(dimensions["demande"], defaut=50)
    score_douleur = _moyenne(dimensions["douleur"], defaut=50)
    score_concurrence = _moyenne(dimensions["concurrence"], defaut=50)
    score_monetisation = _moyenne(dimensions["monetisation"], defaut=50)

    score_global = int(
        score_demande * 0.40
        + score_douleur * 0.20
        + score_concurrence * 0.20
        + score_monetisation * 0.20
    )

    if score_global > 70:
        verdict = "explorer"
    elif score_global >= 50:
        verdict = "watchlist"
    else:
        verdict = "abandonner"

    opportunite = _generer_opportunite(
        score_demande, score_douleur, score_concurrence, score_monetisation,
        score_global, verdict, dimensions,
    )

    return {
        "score_global": score_global,
        "score_demande": score_demande,
        "score_douleur": score_douleur,
        "score_concurrence": score_concurrence,
        "score_monetisation": score_monetisation,
        "verdict": verdict,
        "opportunite": opportunite,
    }


def _moyenne(values: list[int], defaut: int = 50) -> int:
    """Moyenne d'une liste d'entiers, ou valeur par defaut si vide."""
    if not values:
        return defaut
    return int(sum(values) / len(values))


def _generer_opportunite(
    score_demande: int,
    score_douleur: int,
    score_concurrence: int,
    score_monetisation: int,
    score_global: int,
    verdict: str,
    dimensions: dict[str, list[int]],
) -> str:
    """Genere un texte d'opportunite base sur les scores."""
    scores = {
        "demande": score_demande,
        "douleur": score_douleur,
        "concurrence": score_concurrence,
        "monetisation": score_monetisation,
    }
    forces = [k for k, v in scores.items() if v >= 70]
    faiblesses = [k for k, v in scores.items() if v < 40]
    sans_donnees = [k for k, v in dimensions.items() if not v]

    parties = []

    if verdict == "explorer":
        parties.append(f"Score global fort ({score_global}/100).")
        if forces:
            parties.append(f"Points forts : {', '.join(forces)}.")
        if sans_donnees:
            parties.append(f"Analyse incomplete sur : {', '.join(sans_donnees)} — a confirmer.")
    elif verdict == "watchlist":
        parties.append(f"Signal modere ({score_global}/100).")
        if forces:
            parties.append(f"Potentiel sur : {', '.join(forces)}.")
        if faiblesses:
            parties.append(f"Attention sur : {', '.join(faiblesses)}.")
        parties.append("A reevaluer dans 1-2 mois.")
    else:
        parties.append(f"Signal faible ({score_global}/100).")
        if faiblesses:
            parties.append(f"Insuffisant sur : {', '.join(faiblesses)}.")
        parties.append("Ce creneau ne semble pas viable en l'etat.")

    return " ".join(parties)
