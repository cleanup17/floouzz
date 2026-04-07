"""Service de traduction FR→EN via Claude API."""

import json
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


async def traduire_mot_cle(mot_cle: str) -> str:
    """
    Traduit un mot-cle du francais vers l'anglais via Claude API.
    Retourne le mot-cle original si la traduction echoue ou si la cle n'est pas configuree.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — mot-cle non traduit")
        return mot_cle

    # Si le mot-cle est deja en anglais (heuristique simple), pas de traduction
    if mot_cle.isascii() and " " not in mot_cle:
        return mot_cle

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"Traduis ce mot-cle du francais vers l'anglais. Reponds UNIQUEMENT avec la traduction, rien d'autre.\n\n{mot_cle}",
            }],
        )

        traduction = message.content[0].text.strip().strip('"').strip("'")
        logger.info(f"Traduction : '{mot_cle}' → '{traduction}'")
        return traduction

    except Exception as e:
        logger.error(f"Erreur traduction Claude pour '{mot_cle}': {e}")
        return mot_cle
