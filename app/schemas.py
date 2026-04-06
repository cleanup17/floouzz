"""Schémas Pydantic pour la validation des données."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Niches ---

class NicheCreate(BaseModel):
    mot_cle: str = Field(..., min_length=2, max_length=255)


class NicheRead(BaseModel):
    id: uuid.UUID
    mot_cle: str
    created_at: datetime
    nb_analyses: int = 0

    model_config = {"from_attributes": True}


# --- Signaux ---

class SignalRead(BaseModel):
    id: uuid.UUID
    source: str
    donnees: dict
    score_partiel: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Analyses ---

class AnalyseRead(BaseModel):
    id: uuid.UUID
    niche_id: uuid.UUID
    score_global: int | None
    score_demande: int | None
    score_douleur: int | None
    score_concurrence: int | None
    score_monetisation: int | None
    opportunite: str | None
    verdict: str | None
    created_at: datetime
    signaux: list[SignalRead] = []

    model_config = {"from_attributes": True}
