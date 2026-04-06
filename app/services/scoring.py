"""Service de scoring — calcul du score global et du verdict."""


def calculer_score_global(signaux: list[dict]) -> dict:
    """
    Calcule le score global à partir des signaux collectés.

    En Phase 1, seul Google Trends est disponible.
    Le score de demande vient de Google Trends.
    Les autres scores sont mis à 50 (neutre) en attendant les sources Phase 2.

    Retourne un dict avec les scores et le verdict.
    """
    # Récupérer le score Google Trends
    score_demande = 50
    for signal in signaux:
        if signal["source"] == "google_trends":
            score_demande = signal["score_partiel"]

    # Phase 1 : scores non encore alimentés → valeur neutre
    score_douleur = 50
    score_concurrence = 50
    score_monetisation = 50

    # Score global pondéré
    # Demande = 40% (seule source fiable en V1)
    # Autres = 20% chacun (neutres pour l'instant)
    score_global = int(
        score_demande * 0.40
        + score_douleur * 0.20
        + score_concurrence * 0.20
        + score_monetisation * 0.20
    )

    # Verdict selon les règles métier
    if score_global > 70:
        verdict = "explorer"
    elif score_global >= 50:
        verdict = "watchlist"
    else:
        verdict = "abandonner"

    # Opportunité narrative
    opportunite = _generer_opportunite(score_demande, score_global, verdict)

    return {
        "score_global": score_global,
        "score_demande": score_demande,
        "score_douleur": score_douleur,
        "score_concurrence": score_concurrence,
        "score_monetisation": score_monetisation,
        "verdict": verdict,
        "opportunite": opportunite,
    }


def _generer_opportunite(score_demande: int, score_global: int, verdict: str) -> str:
    """Génère un texte d'opportunité basé sur les scores (Phase 1 — sans LLM)."""
    if verdict == "explorer":
        if score_demande >= 70:
            return (
                "Forte demande détectée sur Google Trends. "
                "Le marché montre un intérêt soutenu — à creuser avec les signaux Reddit et Product Hunt en Phase 2."
            )
        return (
            "Score global encourageant. "
            "La demande est présente — l'analyse sera plus précise avec les sources complémentaires."
        )
    elif verdict == "watchlist":
        if score_demande >= 60:
            return (
                "Demande modérée mais existante. "
                "À surveiller — les signaux complémentaires (douleur, concurrence) pourraient révéler une opportunité."
            )
        return (
            "Signal faible mais pas nul. "
            "À réévaluer dans 1-2 mois pour voir si la tendance évolue."
        )
    else:
        if score_demande > 30:
            return (
                "Demande insuffisante pour justifier un investissement immédiat. "
                "Le marché n'est pas encore mûr ou le mot-clé est trop générique."
            )
        return (
            "Très peu de demande détectée. "
            "Ce créneau ne semble pas viable en l'état — envisager un angle différent."
        )
