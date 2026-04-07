"""Service d'enrichissement des signaux via Claude API."""

import json
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


def _build_prompt(
    titre: str,
    donnees: dict,
    thematiques: list[str],
    preferences_ignorees: list[str],
) -> str:
    """Construit le prompt pour Claude."""
    themes_str = ", ".join(thematiques) if thematiques else "IA, SaaS, E-commerce, Marketing, Video, Finance, Metiers, Sante, Education, Innovation"
    ignores_str = ", ".join(preferences_ignorees) if preferences_ignorees else "aucun"

    return f"""Tu es un analyste de marche. Evalue ce signal pour une consultante numerique solo
qui cherche des niches de micro-SaaS ou d'offres de service IA.

SIGNAL :
Titre : {titre}
Donnees : {json.dumps(donnees, ensure_ascii=False, default=str)[:2000]}

THEMATIQUES DISPONIBLES : {themes_str}

SUJETS DEJA IGNORES PAR L'UTILISATRICE : {ignores_str}

Reponds UNIQUEMENT en JSON valide, sans texte avant ou apres :
{{
    "score_pertinence": <entier 0-100>,
    "resume": "<resume en 1-2 phrases en francais>",
    "tags": ["<1 a 3 thematiques parmi la liste>"],
    "mot_cle_suggere": "<mot-cle principal pour approfondir>"
}}"""


async def enrichir_decouverte(
    titre: str,
    donnees: dict,
    thematiques: list[str],
    preferences_ignorees: list[str],
) -> dict:
    """
    Enrichit un signal brut via Claude API.
    Retourne : score_pertinence, resume, tags, mot_cle_suggere.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — enrichissement par defaut")
        return {
            "score_pertinence": 50,
            "resume": titre,
            "tags": [],
            "mot_cle_suggere": None,
        }

    prompt = _build_prompt(titre, donnees, thematiques, preferences_ignorees)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()
        result = json.loads(response_text)

        return {
            "score_pertinence": max(0, min(100, result.get("score_pertinence", 50))),
            "resume": result.get("resume", titre),
            "tags": result.get("tags", [])[:3],
            "mot_cle_suggere": result.get("mot_cle_suggere"),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Reponse Claude non-JSON : {e}")
        return {
            "score_pertinence": 50,
            "resume": titre,
            "tags": [],
            "mot_cle_suggere": None,
        }
    except Exception as e:
        logger.error(f"Erreur enrichissement Claude : {e}")
        return {
            "score_pertinence": 50,
            "resume": titre,
            "tags": [],
            "mot_cle_suggere": None,
        }
