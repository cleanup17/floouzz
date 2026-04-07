"""Schémas Pydantic pour la validation des données."""

import uuid
from datetime import date, datetime

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


# --- Sources ---

class SourceCreate(BaseModel):
    nom: str = Field(..., min_length=2, max_length=100)
    type: str = Field(..., pattern=r"^(serpapi|apify_actor|apify_url|api|webhook)$")
    config: dict = {}
    cle_api_ref: str | None = None
    actif: bool = True
    cron_expr: str = "0 6 * * *"


class SourceUpdate(BaseModel):
    nom: str | None = Field(None, min_length=2, max_length=100)
    type: str | None = Field(None, pattern=r"^(serpapi|apify_actor|apify_url|api|webhook)$")
    config: dict | None = None
    cle_api_ref: str | None = None
    actif: bool | None = None
    cron_expr: str | None = None


class SourceRead(BaseModel):
    id: uuid.UUID
    nom: str
    type: str
    config: dict
    cle_api_ref: str | None
    actif: bool
    cron_expr: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Decouvertes ---

class DecouverteRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    titre: str
    url: str | None
    donnees: dict
    score_pertinence: int | None
    resume: str | None
    tags: list[str]
    statut: str
    mot_cle_suggere: str | None
    niche_id: uuid.UUID | None
    scan_date: date
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Thematiques ---

class ThematiqueCreate(BaseModel):
    nom: str = Field(..., min_length=2, max_length=100)


class ThematiqueRead(BaseModel):
    id: uuid.UUID
    nom: str
    actif: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Webhooks ---

class WebhookSignal(BaseModel):
    """Payload envoye par n8n ou autre systeme externe."""
    source: str = Field(..., min_length=2, max_length=100)
    titre: str = Field(..., min_length=2, max_length=500)
    url: str | None = None
    donnees: dict = {}
    mot_cle_suggere: str | None = None
    token: str = Field(..., min_length=10)


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
