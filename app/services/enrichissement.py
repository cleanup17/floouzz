"""Service d'enrichissement des signaux via Claude API — mode batch."""

import json
import logging
import re

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

MAX_SIGNAUX_PAR_BATCH = 15


def _build_batch_prompt(
    signaux: list[dict],
    thematiques: list[str],
    preferences_ignorees: list[str],
) -> str:
    """Construit un prompt batch pour enrichir plusieurs signaux en un seul appel."""
    themes_str = ", ".join(thematiques) if thematiques else "IA, SaaS, E-commerce, Marketing, Video, Finance, Metiers & RH, Sante, Education, Innovation, Automatisation, Creation"
    ignores_str = ", ".join(preferences_ignorees) if preferences_ignorees else "aucun"

    signaux_text = ""
    for i, s in enumerate(signaux):
        donnees_str = json.dumps(s["donnees"], ensure_ascii=False, default=str)[:500]
        signaux_text += f"\n[{i}] {s['titre']}\nDonnees: {donnees_str}\n"

    return f"""Tu es un analyste de marche. Evalue ces signaux pour une consultante numerique solo
qui cherche des niches de micro-SaaS ou d'offres de service IA.

THEMATIQUES DISPONIBLES : {themes_str}
SUJETS IGNORES : {ignores_str}

SIGNAUX A EVALUER :
{signaux_text}

Pour chaque signal, reponds en JSON. Retourne un tableau JSON, un objet par signal dans l'ordre :
[
  {{"index": 0, "score_pertinence": <0-100>, "resume": "<1-2 phrases en francais>", "tags": ["<1-3 thematiques>"], "mot_cle_suggere": "<mot-cle pour approfondir>"}},
  ...
]

Reponds UNIQUEMENT avec le tableau JSON, rien d'autre."""


async def enrichir_batch(
    signaux: list[dict],
    thematiques: list[str],
    preferences_ignorees: list[str],
) -> list[dict]:
    """
    Enrichit un lot de signaux en un seul appel Claude.
    Chaque signal est un dict avec 'titre' et 'donnees'.
    Retourne une liste de dicts enrichis dans le meme ordre.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — enrichissement par defaut")
        return [
            {"score_pertinence": 50, "resume": s["titre"], "tags": [], "mot_cle_suggere": None}
            for s in signaux
        ]

    prompt = _build_batch_prompt(signaux, thematiques, preferences_ignorees)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip() if message.content else ""
        logger.info(f"Reponse Claude batch ({len(response_text)} chars)")

        # Extraire le tableau JSON
        json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if not json_match:
            logger.warning(f"Pas de JSON tableau dans la reponse Claude")
            return [
                {"score_pertinence": 50, "resume": s["titre"], "tags": [], "mot_cle_suggere": None}
                for s in signaux
            ]

        results = json.loads(json_match.group())

        # Normaliser les resultats
        enrichis = []
        for i, s in enumerate(signaux):
            if i < len(results):
                r = results[i]
                enrichis.append({
                    "score_pertinence": max(0, min(100, r.get("score_pertinence", 50))),
                    "resume": r.get("resume", s["titre"]),
                    "tags": r.get("tags", [])[:3],
                    "mot_cle_suggere": r.get("mot_cle_suggere"),
                })
            else:
                enrichis.append({
                    "score_pertinence": 50,
                    "resume": s["titre"],
                    "tags": [],
                    "mot_cle_suggere": None,
                })

        return enrichis

    except json.JSONDecodeError as e:
        logger.error(f"Reponse Claude non-JSON : {e}")
        return [
            {"score_pertinence": 50, "resume": s["titre"], "tags": [], "mot_cle_suggere": None}
            for s in signaux
        ]
    except Exception as e:
        logger.error(f"Erreur enrichissement Claude batch : {e}")
        return [
            {"score_pertinence": 50, "resume": s["titre"], "tags": [], "mot_cle_suggere": None}
            for s in signaux
        ]


# Compat — enrichir un seul signal (utilise par le code existant si besoin)
async def enrichir_decouverte(
    titre: str,
    donnees: dict,
    thematiques: list[str],
    preferences_ignorees: list[str],
) -> dict:
    """Enrichit un seul signal via batch de 1."""
    results = await enrichir_batch(
        [{"titre": titre, "donnees": donnees}],
        thematiques,
        preferences_ignorees,
    )
    return results[0]
