"""Service de traduction FR→EN via DeepL API Free."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"


async def traduire_mot_cle(mot_cle: str) -> str:
    """
    Traduit un mot-cle du francais vers l'anglais via DeepL.
    Retourne le mot-cle original si la traduction echoue ou si la cle n'est pas configuree.
    """
    if not settings.DEEPL_API_KEY:
        logger.debug("DEEPL_API_KEY non configuree — mot-cle non traduit")
        return mot_cle

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                DEEPL_API_URL,
                data={
                    "auth_key": settings.DEEPL_API_KEY,
                    "text": mot_cle,
                    "source_lang": "FR",
                    "target_lang": "EN",
                },
            )
            response.raise_for_status()
            data = response.json()

        translations = data.get("translations", [])
        if translations:
            traduction = translations[0].get("text", mot_cle)
            logger.info(f"Traduction : '{mot_cle}' → '{traduction}'")
            return traduction

        return mot_cle

    except Exception as e:
        logger.error(f"Erreur traduction DeepL pour '{mot_cle}': {e}")
        return mot_cle
