"""Endpoint webhook pour recevoir des signaux depuis n8n ou autres."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Decouverte, Source
from app.schemas import WebhookSignal

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/signal")
async def recevoir_signal(payload: WebhookSignal, db: AsyncSession = Depends(get_db)):
    """
    Recoit un signal depuis n8n ou autre systeme externe.
    Le token dans le payload doit correspondre a WEBHOOK_TOKEN dans .env.
    """
    if not settings.WEBHOOK_TOKEN or payload.token != settings.WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Token invalide")

    # Trouver ou creer une source webhook
    stmt = select(Source).where(Source.type == "webhook").where(Source.nom == payload.source)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if not source:
        source = Source(
            nom=payload.source,
            type="webhook",
            config={"description": f"Source webhook {payload.source}"},
        )
        db.add(source)
        await db.flush()

    decouverte = Decouverte(
        source_id=source.id,
        titre=payload.titre,
        url=payload.url,
        donnees=payload.donnees,
        mot_cle_suggere=payload.mot_cle_suggere,
        scan_date=date.today(),
        statut="nouveau",
    )
    db.add(decouverte)
    await db.commit()

    return {"status": "ok", "decouverte_id": str(decouverte.id)}
