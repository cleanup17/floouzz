# Floouzz Phase 2 — Mode Decouverte + Multi-sources — Plan d'implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformer Floouzz d'un outil de scoring a la demande en un radar de niches avec scan quotidien automatique, enrichissement Claude, dashboard de signaux, admin sources, et scoring multi-sources.

**Architecture:** Pipeline decouple en 2 etapes (collecte 6h → enrichissement 6h15). Sources configurables en base, cles API dans .env uniquement. Frontend HTMX avec 3 pages (Decouverte, Analyser, Parametres).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, PostgreSQL 16, HTMX, Tailwind CSS, SerpAPI, Apify, DeepL API, Claude API (anthropic SDK), Docker Compose.

---

## Structure fichiers cible

```
app/
├── main.py                          # MODIFIER — ajouter routers + version 0.2
├── config.py                        # MODIFIER — ajouter cles API optionnelles
├── database.py                      # inchange
├── models.py                        # MODIFIER — ajouter Source, Decouverte, Thematique, Preference + mot_cle_en
├── schemas.py                       # MODIFIER — ajouter schemas Phase 2
├── routers/
│   ├── niches.py                    # MODIFIER — utiliser toutes les sources actives
│   ├── decouvertes.py               # CREER — dashboard mode Decouverte
│   ├── sources.py                   # CREER — admin CRUD sources
│   ├── parametres.py                # CREER — parametres + test cles API
│   └── webhooks.py                  # CREER — reception signaux n8n
├── services/
│   ├── scanner.py                   # CREER — orchestrateur scan quotidien
│   ├── enrichissement.py            # CREER — Claude API resume/pertinence/tags
│   ├── scoring.py                   # MODIFIER — scoring multi-sources
│   ├── traduction.py                # CREER — DeepL FR→EN
│   └── sources/
│       ├── base.py                  # CREER — interface commune + dispatcher
│       ├── google_trends.py         # REMPLACER — SerpAPI au lieu de pytrends
│       ├── google_jobs.py           # CREER — SerpAPI Google Jobs
│       ├── google_search.py         # CREER — SerpAPI SERP + CPC + PAA
│       ├── google_news.py           # CREER — SerpAPI Google News
│       ├── reddit.py                # CREER — Apify
│       ├── producthunt.py           # CREER — Apify
│       ├── hackernews.py            # CREER — API Algolia (gratuit, sans cle)
│       └── apify_url.py             # CREER — scraper URL generique Apify
├── templates/
│   ├── base.html                    # MODIFIER — navigation 3 pages
│   ├── decouverte.html              # CREER — dashboard signaux
│   ├── index.html                   # inchange (mode Analyse)
│   ├── historique.html              # inchange
│   ├── parametres.html              # CREER
│   └── partials/
│       ├── signal_carte.html        # CREER — carte signal decouverte
│       ├── source_modale.html       # CREER — modale edition source
│       ├── fiche.html               # inchange
│       └── erreur.html              # inchange
├── static/css/app.css               # MODIFIER — styles Phase 2
tests/
├── conftest.py                      # CREER — fixtures pytest
├── test_models.py                   # CREER
├── test_scoring.py                  # CREER
├── test_traduction.py               # CREER
├── test_enrichissement.py           # CREER
├── test_sources_base.py             # CREER
├── test_scanner.py                  # CREER
├── test_router_decouvertes.py       # CREER
├── test_router_sources.py           # CREER
├── test_router_webhooks.py          # CREER
└── test_router_parametres.py        # CREER
migrations/
├── init.sql                         # inchange
└── phase2.sql                       # CREER — nouvelles tables + ALTER niches
requirements.txt                     # MODIFIER — ajouter anthropic, httpx deja present
.env.example                         # MODIFIER — ajouter cles API Phase 2
```

---

## Task 1 : Migration base de donnees Phase 2

**Files:**
- Create: `migrations/phase2.sql`
- Modify: `docker-compose.yml` (ajouter le fichier de migration)

- [ ] **Step 1: Ecrire le script de migration SQL**

```sql
-- migrations/phase2.sql
-- Floouzz Phase 2 — nouvelles tables + modification niches

-- Ajout traduction anglaise sur niches
ALTER TABLE niches ADD COLUMN IF NOT EXISTS mot_cle_en VARCHAR(255);

-- Sources configurables
CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nom VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook')),
    config JSONB NOT NULL DEFAULT '{}',
    cle_api_ref VARCHAR(50),
    actif BOOLEAN DEFAULT true,
    cron_expr VARCHAR(50) DEFAULT '0 6 * * *',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Decouvertes (signaux du scan quotidien)
CREATE TABLE IF NOT EXISTS decouvertes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    titre VARCHAR(500) NOT NULL,
    url TEXT,
    donnees JSONB NOT NULL DEFAULT '{}',
    score_pertinence INTEGER CHECK (score_pertinence BETWEEN 0 AND 100),
    resume TEXT,
    tags VARCHAR(50)[] DEFAULT '{}',
    statut VARCHAR(20) DEFAULT 'nouveau' CHECK (statut IN ('nouveau', 'vu', 'approfondi', 'ignore')),
    mot_cle_suggere VARCHAR(255),
    niche_id UUID REFERENCES niches(id),
    scan_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Thematiques
CREATE TABLE IF NOT EXISTS thematiques (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nom VARCHAR(100) UNIQUE NOT NULL,
    actif BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Preferences utilisateur (feedback)
CREATE TABLE IF NOT EXISTS preferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(20) NOT NULL CHECK (type IN ('like', 'ignore')),
    decouverte_id UUID NOT NULL REFERENCES decouvertes(id) ON DELETE CASCADE,
    tags_associes VARCHAR(50)[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index
CREATE INDEX IF NOT EXISTS idx_decouvertes_scan_date ON decouvertes(scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_decouvertes_statut ON decouvertes(statut);
CREATE INDEX IF NOT EXISTS idx_decouvertes_tags ON decouvertes USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_decouvertes_source_id ON decouvertes(source_id);
CREATE INDEX IF NOT EXISTS idx_preferences_type ON preferences(type);
CREATE INDEX IF NOT EXISTS idx_sources_actif ON sources(actif);

-- Thematiques par defaut
INSERT INTO thematiques (nom) VALUES
    ('IA'),
    ('Automatisation'),
    ('E-commerce'),
    ('SaaS'),
    ('Marketing'),
    ('Video'),
    ('Metiers & RH'),
    ('Finance'),
    ('Sante'),
    ('Education'),
    ('Creation'),
    ('Innovation')
ON CONFLICT (nom) DO NOTHING;
```

- [ ] **Step 2: Ajouter la migration dans docker-compose.yml**

Modifier `docker-compose.yml`, section `db.volumes` :

```yaml
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./migrations/init.sql:/docker-entrypoint-initdb.d/01-init.sql
      - ./migrations/phase2.sql:/docker-entrypoint-initdb.d/02-phase2.sql
```

Note : renommer `init.sql` en `01-init.sql` dans le mapping pour garantir l'ordre d'execution.

- [ ] **Step 3: Appliquer la migration sur la base existante**

La base existe deja avec les tables Phase 1. Les fichiers `docker-entrypoint-initdb.d/` ne s'executent que sur une base vierge. Pour appliquer sur la base existante :

```bash
docker compose exec db psql -U floouzz -d floouzz -f /docker-entrypoint-initdb.d/02-phase2.sql
```

Verifier :

```bash
docker compose exec db psql -U floouzz -d floouzz -c "\dt"
```

Expected: les tables `sources`, `decouvertes`, `thematiques`, `preferences` apparaissent + la colonne `mot_cle_en` existe sur `niches`.

- [ ] **Step 4: Commit**

```bash
git add migrations/phase2.sql docker-compose.yml
git commit -m "feat: migration Phase 2 — tables sources, decouvertes, thematiques, preferences"
```

---

## Task 2 : Modeles SQLAlchemy + schemas Pydantic Phase 2

**Files:**
- Modify: `app/models.py`
- Modify: `app/schemas.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Ecrire les tests des nouveaux modeles**

```python
# tests/conftest.py
"""Fixtures pytest pour les tests Floouzz."""

import uuid
from datetime import date, datetime, timezone

import pytest


@pytest.fixture
def sample_source_data() -> dict:
    """Donnees d'exemple pour une source SerpAPI."""
    return {
        "nom": "Google Trends FR",
        "type": "serpapi",
        "config": {"engine": "google_trends", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    }


@pytest.fixture
def sample_decouverte_data() -> dict:
    """Donnees d'exemple pour une decouverte."""
    return {
        "titre": "AI invoice automation trending",
        "url": "https://trends.google.com/...",
        "donnees": {"moyenne": 72, "tendance": "hausse"},
        "score_pertinence": 85,
        "resume": "Forte hausse des recherches sur l'automatisation de facturation IA",
        "tags": ["IA", "Finance"],
        "statut": "nouveau",
        "mot_cle_suggere": "ai invoice automation",
        "scan_date": date.today(),
    }
```

```python
# tests/test_models.py
"""Tests des modeles SQLAlchemy Phase 2."""

from app.models import Source, Decouverte, Thematique, Preference


def test_source_model_fields():
    """Verifie que le modele Source a tous les champs attendus."""
    source = Source(
        nom="Google Trends FR",
        type="serpapi",
        config={"engine": "google_trends"},
        cle_api_ref="SERPAPI_KEY",
    )
    assert source.nom == "Google Trends FR"
    assert source.type == "serpapi"
    assert source.actif is True
    assert source.cron_expr == "0 6 * * *"


def test_decouverte_model_fields():
    """Verifie que le modele Decouverte a tous les champs attendus."""
    decouverte = Decouverte(
        titre="Test signal",
        donnees={"test": True},
        statut="nouveau",
    )
    assert decouverte.titre == "Test signal"
    assert decouverte.statut == "nouveau"
    assert decouverte.tags == []


def test_thematique_model_fields():
    """Verifie que le modele Thematique a tous les champs attendus."""
    theme = Thematique(nom="IA")
    assert theme.nom == "IA"
    assert theme.actif is True


def test_preference_model_fields():
    """Verifie que le modele Preference a tous les champs attendus."""
    pref = Preference(type="ignore", tags_associes=["IA", "SaaS"])
    assert pref.type == "ignore"
    assert pref.tags_associes == ["IA", "SaaS"]
```

- [ ] **Step 2: Lancer les tests pour verifier qu'ils echouent**

```bash
docker compose exec app python -m pytest tests/test_models.py -v
```

Expected: FAIL — `ImportError: cannot import name 'Source' from 'app.models'`

- [ ] **Step 3: Ajouter les modeles dans app/models.py**

Ajouter `mot_cle_en` a la classe `Niche` (apres `mot_cle`) :

```python
    mot_cle_en: Mapped[str | None] = mapped_column(String(255))
```

Ajouter `ARRAY` a l'import sqlalchemy, et `Boolean, Date` :

```python
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
```

Ajouter les 4 nouveaux modeles apres la classe `Signal` :

```python
class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    cle_api_ref: Mapped[str | None] = mapped_column(String(50))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    cron_expr: Mapped[str] = mapped_column(String(50), default="0 6 * * *")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook')"
        ),
        Index("idx_sources_actif", "actif"),
    )

    # Relations
    decouvertes: Mapped[list["Decouverte"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Decouverte(Base):
    __tablename__ = "decouvertes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    titre: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    donnees: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_pertinence: Mapped[int | None] = mapped_column()
    resume: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    statut: Mapped[str] = mapped_column(String(20), default="nouveau")
    mot_cle_suggere: Mapped[str | None] = mapped_column(String(255))
    niche_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("niches.id"), nullable=True
    )
    scan_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("score_pertinence BETWEEN 0 AND 100"),
        CheckConstraint("statut IN ('nouveau', 'vu', 'approfondi', 'ignore')"),
        Index("idx_decouvertes_scan_date", scan_date.desc()),
        Index("idx_decouvertes_statut", "statut"),
        Index("idx_decouvertes_source_id", "source_id"),
    )

    # Relations
    source: Mapped["Source"] = relationship(back_populates="decouvertes")
    preferences: Mapped[list["Preference"]] = relationship(
        back_populates="decouverte", cascade="all, delete-orphan"
    )


class Thematique(Base):
    __tablename__ = "thematiques"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nom: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Preference(Base):
    __tablename__ = "preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    decouverte_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decouvertes.id", ondelete="CASCADE"), nullable=False
    )
    tags_associes: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("type IN ('like', 'ignore')"),
        Index("idx_preferences_type", "type"),
    )

    # Relations
    decouverte: Mapped["Decouverte"] = relationship(back_populates="preferences")
```

Ajouter l'import `date` en haut du fichier :

```python
from datetime import date, datetime, timezone
```

- [ ] **Step 4: Ajouter les schemas Pydantic dans app/schemas.py**

Ajouter apres les schemas existants :

```python
from datetime import date

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
```

- [ ] **Step 5: Lancer les tests pour verifier qu'ils passent**

```bash
docker compose exec app python -m pytest tests/test_models.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/schemas.py tests/conftest.py tests/test_models.py
git commit -m "feat: modeles et schemas Phase 2 — Source, Decouverte, Thematique, Preference"
```

---

## Task 3 : Configuration — cles API + nouvelles dependances

**Files:**
- Modify: `app/config.py`
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Mettre a jour la config**

Remplacer le contenu de `app/config.py` :

```python
"""Configuration de l'application Floouzz."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Parametres charges depuis les variables d'environnement."""

    # Base de donnees
    DATABASE_URL: str = "postgresql+asyncpg://floouzz:floouzz_secret@db:5432/floouzz"

    # Application
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # Cles API (optionnelles — les sources qui en ont besoin verifient leur presence)
    SERPAPI_KEY: str | None = None
    APIFY_TOKEN: str | None = None
    DEEPL_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None

    # Webhook
    WEBHOOK_TOKEN: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
```

- [ ] **Step 2: Ajouter les dependances**

Ajouter a `requirements.txt` (en gardant les existantes) :

```
anthropic==0.52.0
```

Note : `httpx` est deja present. `pytrends` reste pour compatibilite le temps de la migration vers SerpAPI.

- [ ] **Step 3: Mettre a jour .env.example**

```env
# Base de donnees PostgreSQL
POSTGRES_USER=floouzz
POSTGRES_PASSWORD=floouzz_secret
POSTGRES_DB=floouzz
DATABASE_URL=postgresql+asyncpg://floouzz:floouzz_secret@db:5432/floouzz

# Application
APP_ENV=development
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000

# Phase 2 — Sources de signaux
SERPAPI_KEY=
APIFY_TOKEN=
DEEPL_API_KEY=
ANTHROPIC_API_KEY=

# Webhook (token pour securiser l'endpoint n8n)
WEBHOOK_TOKEN=
```

- [ ] **Step 4: Rebuild Docker**

```bash
docker compose build app
docker compose up -d
```

Verifier :

```bash
docker compose logs app --tail 5
```

Expected: `Application startup complete.`

- [ ] **Step 5: Commit**

```bash
git add app/config.py requirements.txt .env.example
git commit -m "feat: config Phase 2 — cles API optionnelles + dependance anthropic"
```

---

## Task 4 : Interface commune des sources (base.py)

**Files:**
- Create: `app/services/sources/base.py`
- Create: `tests/test_sources_base.py`

- [ ] **Step 1: Ecrire le test**

```python
# tests/test_sources_base.py
"""Tests du dispatcher de sources."""

import pytest
from app.services.sources.base import get_source_fetcher, SourceResult


def test_source_result_creation():
    """Verifie la creation d'un SourceResult."""
    result = SourceResult(
        titre="Test signal",
        url="https://example.com",
        donnees={"test": True},
        score_partiel=75,
    )
    assert result.titre == "Test signal"
    assert result.score_partiel == 75


def test_source_result_error():
    """Verifie la creation d'un SourceResult en erreur."""
    result = SourceResult.error("Erreur de connexion")
    assert result.titre == "Erreur"
    assert result.score_partiel == 0
    assert "Erreur de connexion" in result.donnees.get("erreur", "")


def test_get_source_fetcher_unknown_type():
    """Verifie qu'un type inconnu retourne None."""
    fetcher = get_source_fetcher("type_inconnu")
    assert fetcher is None


def test_get_source_fetcher_serpapi():
    """Verifie que le type serpapi retourne un fetcher."""
    fetcher = get_source_fetcher("serpapi")
    assert fetcher is not None
    assert callable(fetcher)
```

- [ ] **Step 2: Lancer le test pour verifier qu'il echoue**

```bash
docker compose exec app python -m pytest tests/test_sources_base.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementer base.py**

```python
# app/services/sources/base.py
"""Interface commune pour toutes les sources de signaux."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class SourceResult:
    """Resultat standardise d'un appel a une source."""

    titre: str
    url: str | None = None
    donnees: dict = field(default_factory=dict)
    score_partiel: int = 0

    @classmethod
    def error(cls, message: str) -> SourceResult:
        """Cree un resultat d'erreur."""
        return cls(
            titre="Erreur",
            donnees={"erreur": message},
            score_partiel=0,
        )


# Type d'un fetcher : prend (mot_cle, config) et retourne une liste de SourceResult
FetcherType = Callable[[str, dict], Coroutine[None, None, list[SourceResult]]]

# Registre des fetchers par type de source
_FETCHERS: dict[str, FetcherType] = {}


def register_fetcher(source_type: str, fetcher: FetcherType) -> None:
    """Enregistre un fetcher pour un type de source."""
    _FETCHERS[source_type] = fetcher


def get_source_fetcher(source_type: str) -> FetcherType | None:
    """Retourne le fetcher pour un type de source donne."""
    return _FETCHERS.get(source_type)


async def fetch_source(source_type: str, mot_cle: str, config: dict) -> list[SourceResult]:
    """Appelle le fetcher correspondant au type de source."""
    fetcher = get_source_fetcher(source_type)
    if fetcher is None:
        logger.warning(f"Type de source inconnu : {source_type}")
        return [SourceResult.error(f"Type de source inconnu : {source_type}")]
    try:
        return await fetcher(mot_cle, config)
    except Exception as e:
        logger.error(f"Erreur source {source_type} pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 4: Enregistrer le fetcher serpapi (placeholder pour passer le test)**

Ajouter a la fin de `base.py` :

```python
# Enregistrement des fetchers disponibles
# Les imports sont faits ici pour eviter les imports circulaires
def _register_all() -> None:
    """Enregistre tous les fetchers disponibles."""
    from app.services.sources.google_trends import fetch_serpapi_trends
    register_fetcher("serpapi", fetch_serpapi_trends)

    # Les fetchers suivants seront ajoutes au fur et a mesure :
    # register_fetcher("apify_actor", fetch_apify_actor)
    # register_fetcher("apify_url", fetch_apify_url)
    # register_fetcher("webhook", fetch_webhook)


# Appele au premier import du module
_register_all()
```

Note : `fetch_serpapi_trends` n'existe pas encore, on le cree dans la Task 5. Pour l'instant, creer un placeholder dans `google_trends.py` :

Ajouter a la fin de `app/services/sources/google_trends.py` :

```python
async def fetch_serpapi_trends(mot_cle: str, config: dict) -> list:
    """Fetcher SerpAPI Google Trends (a implementer Task 5)."""
    from app.services.sources.base import SourceResult
    return [SourceResult.error("SerpAPI pas encore implemente")]
```

- [ ] **Step 5: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_sources_base.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/services/sources/base.py app/services/sources/google_trends.py tests/test_sources_base.py
git commit -m "feat: interface commune des sources — base.py + SourceResult + dispatcher"
```

---

## Task 5 : Source SerpAPI — Google Trends

**Files:**
- Modify: `app/services/sources/google_trends.py`

- [ ] **Step 1: Remplacer google_trends.py par la version SerpAPI**

Remplacer tout le contenu de `app/services/sources/google_trends.py` :

```python
"""Source de signaux : Google Trends via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_serpapi_trends(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google Trends via SerpAPI.
    Config attendue : {"engine": "google_trends", "gl": "fr", "hl": "fr"}
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": config.get("engine", "google_trends"),
        "q": mot_cle,
        "geo": config.get("gl", "FR"),
        "hl": config.get("hl", "fr"),
        "date": "today 12-m",
        "data_type": "TIMESERIES",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        # Extraire les donnees de tendance
        timeline = data.get("interest_over_time", {}).get("timeline_data", [])

        if not timeline:
            return [SourceResult(
                titre=f"Google Trends : {mot_cle}",
                donnees={"erreur": "Aucune donnee disponible", "mot_cle": mot_cle},
                score_partiel=0,
            )]

        # Extraire les valeurs
        values = []
        dates = []
        for point in timeline:
            val = point.get("values", [{}])[0].get("extracted_value", 0)
            values.append(val)
            dates.append(point.get("date", ""))

        # Calcul du score (meme logique que Phase 1)
        moyenne = sum(values) / len(values) if values else 0
        derniere_valeur = values[-1] if values else 0
        pic = max(values) if values else 0

        quart = len(values) // 4 if len(values) >= 4 else 1
        debut = sum(values[:quart]) / quart
        fin = sum(values[-quart:]) / quart
        tendance_hausse = min(20, max(0, int((fin - debut) / max(debut, 1) * 50)))

        score_partiel = min(100, int(moyenne * 0.4 + derniere_valeur * 0.4 + tendance_hausse))

        # Requetes associees
        related = data.get("related_queries", {})
        top_queries = []
        if "rising" in related:
            top_queries = [
                {"query": q.get("query", ""), "value": q.get("extracted_value", 0)}
                for q in related["rising"][:5]
            ]

        donnees = {
            "mot_cle": mot_cle,
            "periode": "12 derniers mois",
            "geo": config.get("gl", "FR"),
            "moyenne": round(moyenne, 1),
            "derniere_valeur": derniere_valeur,
            "pic": pic,
            "tendance": "hausse" if fin > debut else "baisse" if fin < debut else "stable",
            "variation_pct": round((fin - debut) / max(debut, 1) * 100, 1),
            "series": dict(zip(dates, values)),
            "requetes_associees": top_queries,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google Trends : {mot_cle} ({donnees['tendance']}, {score_partiel}/100)",
            url=f"https://trends.google.com/trends/explore?q={mot_cle}&geo=FR",
            donnees=donnees,
            score_partiel=score_partiel,
        )]

    except httpx.HTTPStatusError as e:
        logger.error(f"SerpAPI HTTP error pour '{mot_cle}': {e.response.status_code}")
        return [SourceResult.error(f"SerpAPI erreur HTTP {e.response.status_code}")]
    except Exception as e:
        logger.error(f"SerpAPI erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]


# Compat Phase 1 — ancien format pour le router niches.py existant
async def fetch_google_trends(mot_cle: str) -> dict:
    """Wrapper de compatibilite Phase 1 → Phase 2."""
    results = await fetch_serpapi_trends(mot_cle, {"engine": "google_trends", "gl": "FR", "hl": "fr"})
    if results:
        r = results[0]
        return {"donnees": r.donnees, "score_partiel": r.score_partiel}
    return {"donnees": {"erreur": "Aucun resultat"}, "score_partiel": 0}
```

- [ ] **Step 2: Verifier que l'app demarre**

```bash
docker compose logs app --tail 5
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git add app/services/sources/google_trends.py
git commit -m "feat: Google Trends via SerpAPI — remplace pytrends"
```

---

## Task 6 : Sources SerpAPI — Google Jobs, Search, News

**Files:**
- Create: `app/services/sources/google_jobs.py`
- Create: `app/services/sources/google_search.py`
- Create: `app/services/sources/google_news.py`

- [ ] **Step 1: Creer google_jobs.py**

```python
# app/services/sources/google_jobs.py
"""Source de signaux : Google Jobs via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_jobs(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google Jobs via SerpAPI.
    Retourne le nombre d'offres et les principales.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_jobs",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        jobs = data.get("jobs_results", [])
        nb_jobs = len(jobs)

        # Score : plus il y a d'offres, plus le marche est actif
        # 0 offre = 0, 5+ = 50, 15+ = 75, 30+ = 100
        if nb_jobs >= 30:
            score = 100
        elif nb_jobs >= 15:
            score = 75
        elif nb_jobs >= 5:
            score = 50
        elif nb_jobs >= 1:
            score = 25
        else:
            score = 0

        top_jobs = [
            {
                "titre": j.get("title", ""),
                "entreprise": j.get("company_name", ""),
                "lieu": j.get("location", ""),
            }
            for j in jobs[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_offres": nb_jobs,
            "top_offres": top_jobs,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google Jobs : {nb_jobs} offres pour '{mot_cle}'",
            url=f"https://www.google.com/search?q={mot_cle}&ibp=htl;jobs",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Google Jobs erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 2: Creer google_search.py**

```python
# app/services/sources/google_search.py
"""Source de signaux : Google Search via SerpAPI — SERP, CPC, People Also Ask."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_search(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google Search via SerpAPI.
    Extrait : nombre de resultats, CPC des ads, People Also Ask.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        results = []

        # People Also Ask — signal douleur
        paa = data.get("related_questions", [])
        if paa:
            questions = [q.get("question", "") for q in paa[:5]]
            results.append(SourceResult(
                titre=f"People Also Ask : {len(paa)} questions pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "questions": questions,
                    "nb_questions": len(paa),
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=min(100, len(paa) * 15),
            ))

        # Ads — signal monetisation (quelqu'un paie pour ce mot-cle)
        ads = data.get("ads", [])
        nb_ads = len(ads)
        if nb_ads > 0:
            top_ads = [
                {"titre": a.get("title", ""), "lien": a.get("displayed_link", "")}
                for a in ads[:3]
            ]
            # Plus il y a d'ads, plus le marche est monetisable
            score_ads = min(100, nb_ads * 20)
            results.append(SourceResult(
                titre=f"Google Ads : {nb_ads} annonces pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "nb_ads": nb_ads,
                    "top_ads": top_ads,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score_ads,
            ))

        # Nombre total de resultats — signal concurrence
        search_info = data.get("search_information", {})
        total_results = search_info.get("total_results", 0)
        if total_results:
            # Moins de resultats = moins de concurrence = meilleur score
            if total_results < 100_000:
                score_conc = 90
            elif total_results < 1_000_000:
                score_conc = 70
            elif total_results < 10_000_000:
                score_conc = 50
            elif total_results < 100_000_000:
                score_conc = 30
            else:
                score_conc = 10

            results.append(SourceResult(
                titre=f"Google : {total_results:,} resultats pour '{mot_cle}'",
                donnees={
                    "mot_cle": mot_cle,
                    "total_resultats": total_results,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=score_conc,
            ))

        if not results:
            results.append(SourceResult(
                titre=f"Google Search : pas de donnees exploitables pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle},
                score_partiel=0,
            ))

        return results

    except Exception as e:
        logger.error(f"Google Search erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 3: Creer google_news.py**

```python
# app/services/sources/google_news.py
"""Source de signaux : Google News via SerpAPI."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def fetch_google_news(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Google News via SerpAPI.
    Retourne les articles recents et un score d'actualite.
    """
    if not settings.SERPAPI_KEY:
        return [SourceResult.error("SERPAPI_KEY non configuree dans .env")]

    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google_news",
        "q": mot_cle,
        "gl": config.get("gl", "fr"),
        "hl": config.get("hl", "fr"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        news = data.get("news_results", [])
        nb_articles = len(news)

        # Score : sujet dans l'actu = signal de timing
        if nb_articles >= 10:
            score = 90
        elif nb_articles >= 5:
            score = 65
        elif nb_articles >= 2:
            score = 40
        elif nb_articles >= 1:
            score = 20
        else:
            score = 0

        top_articles = [
            {
                "titre": n.get("title", ""),
                "source": n.get("source", {}).get("name", ""),
                "date": n.get("date", ""),
                "lien": n.get("link", ""),
            }
            for n in news[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_articles": nb_articles,
            "top_articles": top_articles,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Google News : {nb_articles} articles pour '{mot_cle}'",
            url=f"https://news.google.com/search?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Google News erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 4: Enregistrer les nouveaux fetchers dans base.py**

Modifier `_register_all()` dans `app/services/sources/base.py` :

```python
def _register_all() -> None:
    """Enregistre tous les fetchers disponibles."""
    from app.services.sources.google_trends import fetch_serpapi_trends
    from app.services.sources.google_jobs import fetch_google_jobs
    from app.services.sources.google_search import fetch_google_search
    from app.services.sources.google_news import fetch_google_news

    register_fetcher("serpapi", fetch_serpapi_trends)
    register_fetcher("serpapi_jobs", fetch_google_jobs)
    register_fetcher("serpapi_search", fetch_google_search)
    register_fetcher("serpapi_news", fetch_google_news)
```

Note : chaque source Google a son propre type pour pouvoir etre configuree independamment dans l'admin.

- [ ] **Step 5: Commit**

```bash
git add app/services/sources/
git commit -m "feat: sources SerpAPI — Google Jobs, Search (CPC/PAA), News"
```

---

## Task 7 : Sources Apify — Reddit, Product Hunt, Hacker News, URL scraper

**Files:**
- Create: `app/services/sources/reddit.py`
- Create: `app/services/sources/producthunt.py`
- Create: `app/services/sources/hackernews.py`
- Create: `app/services/sources/apify_url.py`

- [ ] **Step 1: Creer reddit.py**

```python
# app/services/sources/reddit.py
"""Source de signaux : Reddit via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_reddit(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Reddit via Apify actor.
    Config attendue : {"actor_id": "trudax/reddit-scraper", "input": {"subreddits": [...]}}
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "trudax/reddit-scraper")
    default_input = {
        "searchPosts": True,
        "searches": [mot_cle],
        "sort": "hot",
        "time": "week",
        "maxItems": 30,
    }
    # Fusionner avec la config personnalisee
    actor_input = {**default_input, **config.get("input", {})}
    actor_input["searches"] = [mot_cle]

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"token": settings.APIFY_TOKEN},
                json=actor_input,
            )
            response.raise_for_status()
            posts = response.json()

        if not posts:
            return [SourceResult(
                titre=f"Reddit : aucun post pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "nb_posts": 0},
                score_partiel=0,
            )]

        nb_posts = len(posts)
        total_comments = sum(p.get("numberOfComments", 0) for p in posts)
        total_upvotes = sum(p.get("upVotes", 0) for p in posts)

        # Score : volume de discussions = intensite de la douleur/interet
        if total_comments >= 500:
            score = 95
        elif total_comments >= 200:
            score = 75
        elif total_comments >= 50:
            score = 55
        elif total_comments >= 10:
            score = 30
        else:
            score = 10

        top_posts = [
            {
                "titre": p.get("title", ""),
                "subreddit": p.get("subreddit", ""),
                "upvotes": p.get("upVotes", 0),
                "commentaires": p.get("numberOfComments", 0),
                "url": p.get("url", ""),
            }
            for p in sorted(posts, key=lambda x: x.get("numberOfComments", 0), reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_posts": nb_posts,
            "total_commentaires": total_comments,
            "total_upvotes": total_upvotes,
            "top_posts": top_posts,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Reddit : {nb_posts} posts, {total_comments} commentaires pour '{mot_cle}'",
            url=f"https://www.reddit.com/search/?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Reddit erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 2: Creer producthunt.py**

```python
# app/services/sources/producthunt.py
"""Source de signaux : Product Hunt via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_producthunt(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Product Hunt via Apify actor.
    Retourne les produits existants dans la niche — signal concurrence.
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    actor_id = config.get("actor_id", "dainty_screw/producthunt-scraper")
    actor_input = {
        "search": mot_cle,
        "maxItems": 20,
        **config.get("input", {}),
    }

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"token": settings.APIFY_TOKEN},
                json=actor_input,
            )
            response.raise_for_status()
            products = response.json()

        nb_products = len(products)

        # Score concurrence : plus il y a de produits, plus c'est sature
        # Inverse : beaucoup de produits = faible score (mauvais pour entrer)
        if nb_products >= 20:
            score = 15
        elif nb_products >= 10:
            score = 35
        elif nb_products >= 5:
            score = 55
        elif nb_products >= 1:
            score = 75
        else:
            score = 95  # Aucun produit = opportunite !

        top_products = [
            {
                "nom": p.get("name", ""),
                "tagline": p.get("tagline", ""),
                "votes": p.get("votesCount", 0),
                "url": p.get("url", ""),
            }
            for p in sorted(products, key=lambda x: x.get("votesCount", 0), reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_produits": nb_products,
            "top_produits": top_products,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Product Hunt : {nb_products} produits pour '{mot_cle}'",
            url=f"https://www.producthunt.com/search?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Product Hunt erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 3: Creer hackernews.py**

```python
# app/services/sources/hackernews.py
"""Source de signaux : Hacker News via API Algolia (gratuit, sans cle)."""

import logging
from datetime import datetime, timezone

import httpx

from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1"


async def fetch_hackernews(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Interroge Hacker News via l'API Algolia (gratuit, sans cle).
    Retourne les posts recents sur le sujet.
    """
    params = {
        "query": mot_cle,
        "tags": "story",
        "hitsPerPage": 30,
        "numericFilters": config.get("numeric_filters", "created_at_i>0"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{HN_ALGOLIA_URL}/search_by_date", params=params)
            response.raise_for_status()
            data = response.json()

        hits = data.get("hits", [])
        nb_hits = data.get("nbHits", 0)

        if not hits:
            return [SourceResult(
                titre=f"Hacker News : aucun post pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "nb_posts": 0},
                score_partiel=0,
            )]

        total_points = sum(h.get("points", 0) or 0 for h in hits)
        total_comments = sum(h.get("num_comments", 0) or 0 for h in hits)

        # Score : engagement de la communaute tech
        if total_points >= 500:
            score = 90
        elif total_points >= 200:
            score = 70
        elif total_points >= 50:
            score = 50
        elif total_points >= 10:
            score = 30
        else:
            score = 10

        top_posts = [
            {
                "titre": h.get("title", ""),
                "points": h.get("points", 0),
                "commentaires": h.get("num_comments", 0),
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
                "date": h.get("created_at", ""),
            }
            for h in sorted(hits, key=lambda x: x.get("points", 0) or 0, reverse=True)[:5]
        ]

        donnees = {
            "mot_cle": mot_cle,
            "nb_posts": nb_hits,
            "total_points": total_points,
            "total_commentaires": total_comments,
            "top_posts": top_posts,
            "collecte": datetime.now(timezone.utc).isoformat(),
        }

        return [SourceResult(
            titre=f"Hacker News : {nb_hits} posts, {total_points} points pour '{mot_cle}'",
            url=f"https://hn.algolia.com/?q={mot_cle}",
            donnees=donnees,
            score_partiel=score,
        )]

    except Exception as e:
        logger.error(f"Hacker News erreur pour '{mot_cle}': {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 4: Creer apify_url.py**

```python
# app/services/sources/apify_url.py
"""Source de signaux : scraper URL generique via Apify."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"


async def fetch_apify_url(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Scrape une ou plusieurs URLs via Apify Web Scraper.
    Config attendue : {"urls": ["https://..."], "selector": "article h2"}
    """
    if not settings.APIFY_TOKEN:
        return [SourceResult.error("APIFY_TOKEN non configure dans .env")]

    urls = config.get("urls", [])
    if not urls:
        return [SourceResult.error("Aucune URL configuree pour cette source")]

    actor_id = config.get("actor_id", "apify/web-scraper")
    actor_input = {
        "startUrls": [{"url": u} for u in urls],
        "maxPagesPerCrawl": config.get("max_pages", 10),
        **config.get("input", {}),
    }

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"token": settings.APIFY_TOKEN},
                json=actor_input,
            )
            response.raise_for_status()
            items = response.json()

        if not items:
            return [SourceResult(
                titre=f"URL scraper : aucun resultat pour '{mot_cle}'",
                donnees={"mot_cle": mot_cle, "urls": urls},
                score_partiel=0,
            )]

        results = []
        for item in items[:10]:
            titre = item.get("title", item.get("text", "Sans titre"))[:200]
            results.append(SourceResult(
                titre=titre,
                url=item.get("url", urls[0] if urls else None),
                donnees={
                    "mot_cle": mot_cle,
                    "contenu": item,
                    "collecte": datetime.now(timezone.utc).isoformat(),
                },
                score_partiel=50,  # Score neutre — sera affine par l'enrichissement Claude
            ))

        return results

    except Exception as e:
        logger.error(f"Apify URL scraper erreur : {e}")
        return [SourceResult.error(str(e))]
```

- [ ] **Step 5: Enregistrer tous les fetchers dans base.py**

Mettre a jour `_register_all()` dans `app/services/sources/base.py` :

```python
def _register_all() -> None:
    """Enregistre tous les fetchers disponibles."""
    from app.services.sources.google_trends import fetch_serpapi_trends
    from app.services.sources.google_jobs import fetch_google_jobs
    from app.services.sources.google_search import fetch_google_search
    from app.services.sources.google_news import fetch_google_news
    from app.services.sources.reddit import fetch_reddit
    from app.services.sources.producthunt import fetch_producthunt
    from app.services.sources.hackernews import fetch_hackernews
    from app.services.sources.apify_url import fetch_apify_url

    register_fetcher("serpapi", fetch_serpapi_trends)
    register_fetcher("serpapi_jobs", fetch_google_jobs)
    register_fetcher("serpapi_search", fetch_google_search)
    register_fetcher("serpapi_news", fetch_google_news)
    register_fetcher("apify_actor", fetch_reddit)  # Reddit par defaut
    register_fetcher("apify_producthunt", fetch_producthunt)
    register_fetcher("apify_hackernews", fetch_hackernews)
    register_fetcher("apify_url", fetch_apify_url)
```

- [ ] **Step 6: Commit**

```bash
git add app/services/sources/
git commit -m "feat: sources Apify — Reddit, Product Hunt, Hacker News, URL scraper"
```

---

## Task 8 : Service traduction (DeepL)

**Files:**
- Create: `app/services/traduction.py`
- Create: `tests/test_traduction.py`

- [ ] **Step 1: Ecrire le test**

```python
# tests/test_traduction.py
"""Tests du service de traduction DeepL."""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.traduction import traduire_mot_cle


@pytest.mark.asyncio
async def test_traduire_retourne_traduction():
    """Verifie que la traduction fonctionne avec un mock DeepL."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "translations": [{"text": "accounting automation"}]
    }
    mock_response.raise_for_status = lambda: None

    with patch("app.services.traduction.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        result = await traduire_mot_cle("automatisation comptable")
        assert result == "accounting automation"


@pytest.mark.asyncio
async def test_traduire_sans_cle_retourne_original():
    """Sans cle DeepL, retourne le mot-cle original."""
    with patch("app.services.traduction.settings") as mock_settings:
        mock_settings.DEEPL_API_KEY = None
        result = await traduire_mot_cle("test")
        assert result == "test"
```

- [ ] **Step 2: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_traduction.py -v
```

Expected: FAIL

- [ ] **Step 3: Implementer traduction.py**

```python
# app/services/traduction.py
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
```

- [ ] **Step 4: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_traduction.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/traduction.py tests/test_traduction.py
git commit -m "feat: service traduction DeepL FR→EN"
```

---

## Task 9 : Service enrichissement (Claude API)

**Files:**
- Create: `app/services/enrichissement.py`
- Create: `tests/test_enrichissement.py`

- [ ] **Step 1: Ecrire le test**

```python
# tests/test_enrichissement.py
"""Tests du service d'enrichissement Claude."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.enrichissement import enrichir_decouverte, _build_prompt


def test_build_prompt_contient_titre():
    """Verifie que le prompt contient le titre du signal."""
    prompt = _build_prompt(
        titre="AI tools trending",
        donnees={"test": True},
        thematiques=["IA", "SaaS"],
        preferences_ignorees=["crypto"],
    )
    assert "AI tools trending" in prompt
    assert "IA" in prompt
    assert "crypto" in prompt


@pytest.mark.asyncio
async def test_enrichir_sans_cle_retourne_defaut():
    """Sans cle Anthropic, retourne des valeurs par defaut."""
    with patch("app.services.enrichissement.settings") as mock_settings:
        mock_settings.ANTHROPIC_API_KEY = None
        result = await enrichir_decouverte("Test signal", {"test": True}, [], [])
        assert result["score_pertinence"] == 50
        assert result["tags"] == []
        assert "resume" in result
```

- [ ] **Step 2: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_enrichissement.py -v
```

Expected: FAIL

- [ ] **Step 3: Implementer enrichissement.py**

```python
# app/services/enrichissement.py
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

        # Extraire le JSON de la reponse
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
```

- [ ] **Step 4: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_enrichissement.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/enrichissement.py tests/test_enrichissement.py
git commit -m "feat: service enrichissement Claude API — resume, pertinence, tags"
```

---

## Task 10 : Service scanner (orchestrateur)

**Files:**
- Create: `app/services/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Ecrire le test**

```python
# tests/test_scanner.py
"""Tests du service scanner."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date

from app.services.scanner import _collect_from_source
from app.services.sources.base import SourceResult


@pytest.mark.asyncio
async def test_collect_from_source_stocke_resultats():
    """Verifie que les resultats d'une source sont transformes en decouvertes."""
    mock_results = [
        SourceResult(
            titre="Test signal",
            url="https://example.com",
            donnees={"test": True},
            score_partiel=75,
        )
    ]

    with patch("app.services.scanner.fetch_source", return_value=mock_results):
        decouvertes = await _collect_from_source(
            source_type="serpapi",
            mot_cle="test",
            config={},
            source_id="fake-uuid",
        )
        assert len(decouvertes) == 1
        assert decouvertes[0]["titre"] == "Test signal"
        assert decouvertes[0]["donnees"]["test"] is True
```

- [ ] **Step 2: Lancer le test**

```bash
docker compose exec app python -m pytest tests/test_scanner.py -v
```

Expected: FAIL

- [ ] **Step 3: Implementer scanner.py**

```python
# app/services/scanner.py
"""Orchestrateur du scan quotidien — collecte + enrichissement."""

import asyncio
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Decouverte, Preference, Source, Thematique
from app.services.enrichissement import enrichir_decouverte
from app.services.sources.base import SourceResult, fetch_source
from app.services.traduction import traduire_mot_cle

logger = logging.getLogger(__name__)


async def _collect_from_source(
    source_type: str,
    mot_cle: str,
    config: dict,
    source_id: str,
) -> list[dict]:
    """Collecte les signaux d'une source et les formate en dict decouverte."""
    results = await fetch_source(source_type, mot_cle, config)
    decouvertes = []
    for r in results:
        decouvertes.append({
            "source_id": source_id,
            "titre": r.titre,
            "url": r.url,
            "donnees": r.donnees,
            "score_partiel": r.score_partiel,
            "scan_date": date.today(),
        })
    return decouvertes


async def run_collecte(mots_cles: list[str] | None = None) -> int:
    """
    Etape 1 : collecte des signaux depuis toutes les sources actives.
    Si mots_cles est None, utilise des mots-cles generiques pour le scan trending.
    Retourne le nombre de decouvertes stockees.
    """
    async with async_session() as db:
        # Charger les sources actives
        stmt = select(Source).where(Source.actif.is_(True))
        result = await db.execute(stmt)
        sources = result.scalars().all()

        if not sources:
            logger.warning("Aucune source active configuree")
            return 0

        # Mots-cles par defaut pour le scan trending
        if not mots_cles:
            mots_cles = ["AI tools", "SaaS", "automation", "no-code", "freelance"]

        total = 0
        for source in sources:
            for mot_cle in mots_cles:
                try:
                    # Traduire si necessaire
                    mot_cle_en = await traduire_mot_cle(mot_cle)

                    # Determiner le type de fetcher selon la config de la source
                    source_type = source.type
                    # Les sources SerpAPI specifiques ont un type dans la config
                    if source.type == "serpapi" and "engine" in source.config:
                        engine = source.config["engine"]
                        if engine == "google_jobs":
                            source_type = "serpapi_jobs"
                        elif engine == "google":
                            source_type = "serpapi_search"
                        elif engine == "google_news":
                            source_type = "serpapi_news"

                    decouvertes_data = await _collect_from_source(
                        source_type=source_type,
                        mot_cle=mot_cle_en,
                        config=source.config,
                        source_id=str(source.id),
                    )

                    for d in decouvertes_data:
                        decouverte = Decouverte(
                            source_id=source.id,
                            titre=d["titre"],
                            url=d.get("url"),
                            donnees=d["donnees"],
                            scan_date=d["scan_date"],
                            statut="nouveau",
                        )
                        db.add(decouverte)
                        total += 1

                except Exception as e:
                    logger.error(f"Erreur collecte {source.nom} / {mot_cle}: {e}")
                    continue

        await db.commit()
        logger.info(f"Collecte terminee : {total} decouvertes stockees")
        return total


async def run_enrichissement() -> int:
    """
    Etape 2 : enrichit les decouvertes brutes du jour via Claude API.
    Retourne le nombre de decouvertes enrichies.
    """
    async with async_session() as db:
        # Charger les thematiques actives
        stmt_themes = select(Thematique).where(Thematique.actif.is_(True))
        result_themes = await db.execute(stmt_themes)
        thematiques = [t.nom for t in result_themes.scalars().all()]

        # Charger les preferences "ignore" recentes pour le filtrage
        stmt_prefs = (
            select(Preference)
            .where(Preference.type == "ignore")
            .order_by(Preference.created_at.desc())
            .limit(50)
        )
        result_prefs = await db.execute(stmt_prefs)
        preferences_ignorees = []
        for p in result_prefs.scalars().all():
            preferences_ignorees.extend(p.tags_associes)
        preferences_ignorees = list(set(preferences_ignorees))

        # Charger les decouvertes non enrichies du jour
        stmt = (
            select(Decouverte)
            .where(Decouverte.scan_date == date.today())
            .where(Decouverte.resume.is_(None))
        )
        result = await db.execute(stmt)
        decouvertes = result.scalars().all()

        if not decouvertes:
            logger.info("Aucune decouverte a enrichir")
            return 0

        total = 0
        for decouverte in decouvertes:
            try:
                enrichi = await enrichir_decouverte(
                    titre=decouverte.titre,
                    donnees=decouverte.donnees,
                    thematiques=thematiques,
                    preferences_ignorees=preferences_ignorees,
                )

                decouverte.score_pertinence = enrichi["score_pertinence"]
                decouverte.resume = enrichi["resume"]
                decouverte.tags = enrichi["tags"]
                decouverte.mot_cle_suggere = enrichi.get("mot_cle_suggere")
                total += 1

            except Exception as e:
                logger.error(f"Erreur enrichissement decouverte {decouverte.id}: {e}")
                continue

        await db.commit()
        logger.info(f"Enrichissement termine : {total} decouvertes enrichies")
        return total


async def run_scan_complet(mots_cles: list[str] | None = None) -> dict:
    """Lance collecte + enrichissement (utilise par le bouton Rafraichir)."""
    nb_collectes = await run_collecte(mots_cles)
    nb_enrichies = await run_enrichissement()
    return {
        "nb_collectes": nb_collectes,
        "nb_enrichies": nb_enrichies,
        "date": date.today().isoformat(),
    }
```

- [ ] **Step 4: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_scanner.py -v
```

Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app/services/scanner.py tests/test_scanner.py
git commit -m "feat: scanner — orchestrateur collecte + enrichissement"
```

---

## Task 11 : Scoring multi-sources

**Files:**
- Modify: `app/services/scoring.py`
- Create: `tests/test_scoring.py`

- [ ] **Step 1: Ecrire les tests**

```python
# tests/test_scoring.py
"""Tests du service de scoring."""

from app.services.scoring import calculer_score_global


def test_score_global_toutes_sources():
    """Score avec toutes les sources alimentees."""
    signaux = [
        {"source": "google_trends", "score_partiel": 80},
        {"source": "google_news", "score_partiel": 60},
        {"source": "reddit", "score_partiel": 70},
        {"source": "hackernews", "score_partiel": 50},
        {"source": "google_search_paa", "score_partiel": 65},
        {"source": "producthunt", "score_partiel": 75},
        {"source": "google_search_concurrence", "score_partiel": 60},
        {"source": "google_jobs", "score_partiel": 85},
        {"source": "google_search_ads", "score_partiel": 70},
    ]
    result = calculer_score_global(signaux)
    assert 0 <= result["score_global"] <= 100
    assert result["verdict"] in ("explorer", "watchlist", "abandonner")
    assert result["score_demande"] != 50  # Plus la valeur par defaut


def test_score_global_aucune_source():
    """Score avec aucune source — tout a 50 par defaut."""
    result = calculer_score_global([])
    assert result["score_global"] == 50
    assert result["verdict"] == "watchlist"


def test_verdict_explorer():
    """Un score > 70 donne le verdict 'explorer'."""
    signaux = [
        {"source": "google_trends", "score_partiel": 95},
        {"source": "reddit", "score_partiel": 90},
        {"source": "producthunt", "score_partiel": 85},
        {"source": "google_jobs", "score_partiel": 90},
    ]
    result = calculer_score_global(signaux)
    assert result["verdict"] == "explorer"


def test_verdict_abandonner():
    """Un score < 50 donne le verdict 'abandonner'."""
    signaux = [
        {"source": "google_trends", "score_partiel": 10},
        {"source": "reddit", "score_partiel": 5},
        {"source": "producthunt", "score_partiel": 90},  # Pas de concurrence = bien
        {"source": "google_jobs", "score_partiel": 0},
    ]
    result = calculer_score_global(signaux)
    assert result["verdict"] == "abandonner"
```

- [ ] **Step 2: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_scoring.py -v
```

Expected: certains FAIL (le scoring actuel ne gere pas les nouvelles sources)

- [ ] **Step 3: Remplacer scoring.py**

```python
# app/services/scoring.py
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
    # Regrouper les scores par dimension
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

    # Calculer la moyenne par dimension (50 si aucun signal)
    score_demande = _moyenne(dimensions["demande"], defaut=50)
    score_douleur = _moyenne(dimensions["douleur"], defaut=50)
    score_concurrence = _moyenne(dimensions["concurrence"], defaut=50)
    score_monetisation = _moyenne(dimensions["monetisation"], defaut=50)

    # Score global pondere
    score_global = int(
        score_demande * 0.40
        + score_douleur * 0.20
        + score_concurrence * 0.20
        + score_monetisation * 0.20
    )

    # Verdict
    if score_global > 70:
        verdict = "explorer"
    elif score_global >= 50:
        verdict = "watchlist"
    else:
        verdict = "abandonner"

    # Opportunite narrative
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
    # Identifier les forces et faiblesses
    scores = {
        "demande": score_demande,
        "douleur": score_douleur,
        "concurrence": score_concurrence,
        "monetisation": score_monetisation,
    }
    forces = [k for k, v in scores.items() if v >= 70]
    faiblesses = [k for k, v in scores.items() if v < 40]

    # Identifier les dimensions sans donnees reelles
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
```

- [ ] **Step 4: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_scoring.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/scoring.py tests/test_scoring.py
git commit -m "feat: scoring multi-sources — 4 dimensions avec moyenne ponderee"
```

---

## Task 12 : Router decouvertes (dashboard mode Decouverte)

**Files:**
- Create: `app/routers/decouvertes.py`
- Create: `app/templates/decouverte.html`
- Create: `app/templates/partials/signal_carte.html`

- [ ] **Step 1: Creer le router decouvertes.py**

```python
# app/routers/decouvertes.py
"""Routes du mode Decouverte — dashboard de signaux quotidiens."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Decouverte, Niche, Preference, Source, Thematique
from app.services.scanner import run_scan_complet

router = APIRouter(prefix="/decouverte", tags=["decouverte"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def page_decouverte(
    request: Request,
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard des signaux du jour."""
    # Signaux du jour, filtres par tag si specifie
    stmt = (
        select(Decouverte)
        .where(Decouverte.scan_date == date.today())
        .where(Decouverte.score_pertinence >= 30)
        .where(Decouverte.statut != "ignore")
        .order_by(Decouverte.score_pertinence.desc())
    )
    if tag:
        stmt = stmt.where(Decouverte.tags.any(tag))

    result = await db.execute(stmt)
    signaux = result.scalars().all()

    # Compter les signaux par tag pour les filtres
    stmt_tags = (
        select(func.unnest(Decouverte.tags).label("tag"), func.count().label("nb"))
        .where(Decouverte.scan_date == date.today())
        .where(Decouverte.score_pertinence >= 30)
        .where(Decouverte.statut != "ignore")
        .group_by("tag")
        .order_by(func.count().desc())
    )
    result_tags = await db.execute(stmt_tags)
    tags_count = {row[0]: row[1] for row in result_tags.all()}

    # Thematiques configurees
    stmt_themes = select(Thematique).where(Thematique.actif.is_(True)).order_by(Thematique.nom)
    result_themes = await db.execute(stmt_themes)
    thematiques = result_themes.scalars().all()

    # Stats
    stmt_total = (
        select(func.count(Decouverte.id))
        .where(Decouverte.scan_date == date.today())
    )
    result_total = await db.execute(stmt_total)
    total_signaux = result_total.scalar() or 0

    return templates.TemplateResponse("decouverte.html", {
        "request": request,
        "signaux": signaux,
        "tags_count": tags_count,
        "thematiques": thematiques,
        "tag_actif": tag,
        "total_signaux": total_signaux,
        "nb_affiches": len(signaux),
        "date_scan": date.today(),
    })


@router.post("/rafraichir", response_class=HTMLResponse)
async def rafraichir(request: Request, db: AsyncSession = Depends(get_db)):
    """Relance un scan complet (collecte + enrichissement)."""
    result = await run_scan_complet()
    # Recharger la page decouverte avec les nouveaux signaux
    return await page_decouverte(request, tag=None, db=db)


@router.post("/ignorer/{decouverte_id}", response_class=HTMLResponse)
async def ignorer_signal(
    request: Request,
    decouverte_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Marque un signal comme ignore et enregistre la preference."""
    stmt = select(Decouverte).where(Decouverte.id == decouverte_id)
    result = await db.execute(stmt)
    decouverte = result.scalar_one_or_none()

    if decouverte:
        decouverte.statut = "ignore"
        pref = Preference(
            type="ignore",
            decouverte_id=decouverte.id,
            tags_associes=decouverte.tags or [],
        )
        db.add(pref)
        await db.commit()

    # Retourner un fragment vide (la carte disparait)
    return HTMLResponse("")


@router.post("/approfondir/{decouverte_id}", response_class=HTMLResponse)
async def approfondir_signal(
    request: Request,
    decouverte_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Approfondir un signal — cree la niche et redirige vers l'analyse."""
    stmt = select(Decouverte).where(Decouverte.id == decouverte_id)
    result = await db.execute(stmt)
    decouverte = result.scalar_one_or_none()

    if not decouverte:
        return templates.TemplateResponse("partials/erreur.html", {
            "request": request,
            "message": "Signal introuvable.",
        })

    mot_cle = decouverte.mot_cle_suggere or decouverte.titre[:100]
    decouverte.statut = "approfondi"

    # Enregistrer la preference positive
    pref = Preference(
        type="like",
        decouverte_id=decouverte.id,
        tags_associes=decouverte.tags or [],
    )
    db.add(pref)
    await db.commit()

    # Retourner un script HTMX qui redirige vers la page d'analyse
    # avec le mot-cle pre-rempli
    return HTMLResponse(
        f'<script>window.location.href="/analyser?mot_cle={mot_cle}";</script>'
    )
```

- [ ] **Step 2: Creer le template decouverte.html**

```html
{# app/templates/decouverte.html #}
{% extends "base.html" %}

{% block title %}Decouverte{% endblock %}

{% block content %}
<div class="max-w-3xl mx-auto">

    <!-- En-tete -->
    <div class="flex items-center justify-between mb-6">
        <div>
            <h1 class="text-2xl font-bold text-white">Signaux du jour</h1>
            <p class="text-sm text-gray-500 mt-1">
                {{ date_scan.strftime('%d/%m/%Y') }} — {{ nb_affiches }} signal{{ 's' if nb_affiches > 1 else '' }} pertinent{{ 's' if nb_affiches > 1 else '' }} / {{ total_signaux }} collecte{{ 's' if total_signaux > 1 else '' }}
            </p>
        </div>
        <button
            hx-post="/decouverte/rafraichir"
            hx-target="#contenu-signaux"
            hx-swap="innerHTML"
            hx-indicator="#loader-refresh"
            class="px-4 py-2 bg-floouzz-600 hover:bg-floouzz-700 text-white text-sm font-medium rounded-lg transition flex items-center gap-2"
        >
            <span class="btn-text">Rafraichir</span>
            <span id="loader-refresh" class="loader hidden items-center gap-2">
                <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                </svg>
                Scan...
            </span>
        </button>
    </div>

    <!-- Filtres thematiques -->
    <div class="flex flex-wrap gap-2 mb-6">
        <a href="/decouverte/"
           class="px-3 py-1 rounded-full text-sm font-medium transition {% if not tag_actif %}bg-floouzz-600 text-white{% else %}bg-gray-800 text-gray-400 hover:bg-gray-700{% endif %}">
            Tous ({{ total_signaux }})
        </a>
        {% for tag, count in tags_count.items() %}
        <a href="/decouverte/?tag={{ tag }}"
           class="px-3 py-1 rounded-full text-sm font-medium transition {% if tag_actif == tag %}bg-floouzz-600 text-white{% else %}bg-gray-800 text-gray-400 hover:bg-gray-700{% endif %}">
            {{ tag }} ({{ count }})
        </a>
        {% endfor %}
    </div>

    <!-- Liste des signaux -->
    <div id="contenu-signaux" class="space-y-4">
        {% if signaux %}
            {% for signal in signaux %}
                {% include "partials/signal_carte.html" %}
            {% endfor %}
        {% else %}
            <div class="text-center py-12 text-gray-500">
                <p class="text-lg">Aucun signal aujourd'hui</p>
                <p class="text-sm mt-2">Clique sur "Rafraichir" pour lancer un scan, ou verifie tes sources dans les parametres.</p>
            </div>
        {% endif %}
    </div>

</div>
{% endblock %}
```

- [ ] **Step 3: Creer le template partials/signal_carte.html**

```html
{# app/templates/partials/signal_carte.html #}
{# Variable attendue : signal (objet Decouverte) #}
<div id="signal-{{ signal.id }}" class="bg-gray-900 border border-gray-700 rounded-lg p-5 transition hover:border-gray-600">
    <div class="flex items-start justify-between gap-4">
        <!-- Score + contenu -->
        <div class="flex gap-4 flex-1">
            <!-- Badge score -->
            <div class="flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center font-bold text-lg
                {% if signal.score_pertinence and signal.score_pertinence > 70 %}bg-green-900/50 text-green-400 border border-green-800
                {% elif signal.score_pertinence and signal.score_pertinence >= 50 %}bg-yellow-900/50 text-yellow-400 border border-yellow-800
                {% else %}bg-red-900/50 text-red-400 border border-red-800{% endif %}">
                {{ signal.score_pertinence or '?' }}
            </div>

            <div class="flex-1 min-w-0">
                <!-- Titre -->
                <h3 class="text-white font-medium truncate">{{ signal.titre }}</h3>

                <!-- Resume -->
                {% if signal.resume %}
                <p class="text-gray-400 text-sm mt-1">{{ signal.resume }}</p>
                {% endif %}

                <!-- Tags -->
                {% if signal.tags %}
                <div class="flex flex-wrap gap-1 mt-2">
                    {% for tag in signal.tags %}
                    <a href="/decouverte/?tag={{ tag }}" class="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300 hover:bg-gray-700 transition">{{ tag }}</a>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </div>

        <!-- Actions -->
        <div class="flex gap-2 flex-shrink-0">
            <button
                hx-post="/decouverte/ignorer/{{ signal.id }}"
                hx-target="#signal-{{ signal.id }}"
                hx-swap="outerHTML"
                class="px-3 py-1 text-sm text-gray-500 hover:text-red-400 hover:bg-red-900/20 rounded transition"
                title="Pas pertinent"
            >
                ✕
            </button>
            <button
                hx-post="/decouverte/approfondir/{{ signal.id }}"
                hx-target="#signal-{{ signal.id }}"
                hx-swap="outerHTML"
                class="px-3 py-1 text-sm bg-floouzz-600/20 text-floouzz-400 hover:bg-floouzz-600/40 rounded transition"
            >
                Approfondir
            </button>
        </div>
    </div>
</div>
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/decouvertes.py app/templates/decouverte.html app/templates/partials/signal_carte.html
git commit -m "feat: dashboard Decouverte — signaux du jour, filtres, approfondir/ignorer"
```

---

## Task 13 : Router sources + parametres (admin)

**Files:**
- Create: `app/routers/sources.py`
- Create: `app/routers/parametres.py`
- Create: `app/templates/parametres.html`
- Create: `app/templates/partials/source_modale.html`

- [ ] **Step 1: Creer le router sources.py**

```python
# app/routers/sources.py
"""Routes CRUD pour l'admin des sources."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Source
from app.schemas import SourceCreate, SourceUpdate
from app.services.sources.base import fetch_source

router = APIRouter(prefix="/api/sources", tags=["sources"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def liste_sources(request: Request, db: AsyncSession = Depends(get_db)):
    """Liste toutes les sources configurees."""
    stmt = select(Source).order_by(Source.created_at)
    result = await db.execute(stmt)
    sources = result.scalars().all()
    return templates.TemplateResponse("partials/source_modale.html", {
        "request": request,
        "sources": sources,
        "mode": "liste",
    })


@router.post("/", response_class=HTMLResponse)
async def creer_source(request: Request, db: AsyncSession = Depends(get_db)):
    """Cree une nouvelle source."""
    form = await request.form()
    source_data = SourceCreate(
        nom=form.get("nom", ""),
        type=form.get("type", ""),
        config={} if not form.get("config") else __import__("json").loads(form.get("config")),
        cle_api_ref=form.get("cle_api_ref") or None,
        actif=form.get("actif") == "on",
        cron_expr=form.get("cron_expr", "0 6 * * *"),
    )
    source = Source(**source_data.model_dump())
    db.add(source)
    await db.commit()
    return await liste_sources(request, db)


@router.put("/{source_id}", response_class=HTMLResponse)
async def modifier_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Modifie une source existante."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if not source:
        return HTMLResponse("Source introuvable", status_code=404)

    form = await request.form()
    if form.get("nom"):
        source.nom = form["nom"]
    if form.get("type"):
        source.type = form["type"]
    if form.get("config"):
        source.config = __import__("json").loads(form["config"])
    if form.get("cle_api_ref") is not None:
        source.cle_api_ref = form["cle_api_ref"] or None
    source.actif = form.get("actif") == "on"
    if form.get("cron_expr"):
        source.cron_expr = form["cron_expr"]
    source.updated_at = datetime.now(timezone.utc)

    await db.commit()
    return await liste_sources(request, db)


@router.delete("/{source_id}", response_class=HTMLResponse)
async def supprimer_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Supprime une source."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source:
        await db.delete(source)
        await db.commit()

    return await liste_sources(request, db)


@router.post("/{source_id}/tester", response_class=HTMLResponse)
async def tester_source(
    request: Request,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Teste une source en lancant un appel reel."""
    stmt = select(Source).where(Source.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if not source:
        return HTMLResponse("Source introuvable", status_code=404)

    results = await fetch_source(source.type, "test", source.config)
    resultats_html = "<div class='bg-gray-800 rounded p-3 mt-2 text-sm'>"
    for r in results:
        resultats_html += f"<p class='text-gray-300'><b>{r.titre}</b> — score: {r.score_partiel}</p>"
        if r.donnees.get("erreur"):
            resultats_html += f"<p class='text-red-400'>{r.donnees['erreur']}</p>"
    resultats_html += "</div>"

    return HTMLResponse(resultats_html)
```

- [ ] **Step 2: Creer le router parametres.py**

```python
# app/routers/parametres.py
"""Routes pour la page parametres."""

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Decouverte, Source, Thematique

router = APIRouter(prefix="/parametres", tags=["parametres"])
templates = Jinja2Templates(directory="app/templates")


def _masquer_cle(cle: str | None) -> str:
    """Masque une cle API : affiche les 4 premiers et 4 derniers caracteres."""
    if not cle:
        return "Non configuree"
    if len(cle) <= 8:
        return "****"
    return f"{cle[:4]}...{cle[-4:]}"


@router.get("/", response_class=HTMLResponse)
async def page_parametres(request: Request, db: AsyncSession = Depends(get_db)):
    """Page parametres : cles API, sources, thematiques, stats scan."""
    # Etat des cles API (masquees)
    cles_api = [
        {"nom": "SERPAPI_KEY", "masquee": _masquer_cle(settings.SERPAPI_KEY), "configuree": bool(settings.SERPAPI_KEY)},
        {"nom": "APIFY_TOKEN", "masquee": _masquer_cle(settings.APIFY_TOKEN), "configuree": bool(settings.APIFY_TOKEN)},
        {"nom": "DEEPL_API_KEY", "masquee": _masquer_cle(settings.DEEPL_API_KEY), "configuree": bool(settings.DEEPL_API_KEY)},
        {"nom": "ANTHROPIC_API_KEY", "masquee": _masquer_cle(settings.ANTHROPIC_API_KEY), "configuree": bool(settings.ANTHROPIC_API_KEY)},
    ]

    # Sources
    stmt_sources = select(Source).order_by(Source.created_at)
    result_sources = await db.execute(stmt_sources)
    sources = result_sources.scalars().all()

    # Thematiques
    stmt_themes = select(Thematique).order_by(Thematique.nom)
    result_themes = await db.execute(stmt_themes)
    thematiques = result_themes.scalars().all()

    # Stats dernier scan
    stmt_stats = select(func.count(Decouverte.id)).where(
        Decouverte.scan_date == func.current_date()
    )
    result_stats = await db.execute(stmt_stats)
    nb_scan_jour = result_stats.scalar() or 0

    return templates.TemplateResponse("parametres.html", {
        "request": request,
        "cles_api": cles_api,
        "sources": sources,
        "thematiques": thematiques,
        "nb_scan_jour": nb_scan_jour,
    })


@router.post("/thematiques", response_class=HTMLResponse)
async def ajouter_thematique(request: Request, db: AsyncSession = Depends(get_db)):
    """Ajoute une thematique."""
    form = await request.form()
    nom = form.get("nom", "").strip()
    if nom and len(nom) >= 2:
        theme = Thematique(nom=nom)
        db.add(theme)
        await db.commit()
    return await page_parametres(request, db)


@router.delete("/thematiques/{theme_id}", response_class=HTMLResponse)
async def supprimer_thematique(
    request: Request,
    theme_id,
    db: AsyncSession = Depends(get_db),
):
    """Supprime une thematique."""
    import uuid as uuid_mod
    stmt = select(Thematique).where(Thematique.id == uuid_mod.UUID(str(theme_id)))
    result = await db.execute(stmt)
    theme = result.scalar_one_or_none()
    if theme:
        await db.delete(theme)
        await db.commit()
    return await page_parametres(request, db)
```

- [ ] **Step 3: Creer le template parametres.html**

Fichier complet a creer : `app/templates/parametres.html` avec les 4 sections (cles API, sources, thematiques, stats scan). Voir le design Section 3 du spec pour le contenu exact.

Le template doit inclure :
- Section cles API avec badges vert/rouge selon `configuree`
- Section sources avec liste + boutons Modifier/Tester/Supprimer (appels HTMX)
- Modale ajout source (formulaire avec champs nom, type, config JSON, cle_api_ref, actif, cron)
- Section thematiques avec badges + formulaire ajout + boutons supprimer
- Section stats scan

- [ ] **Step 4: Creer le template partials/source_modale.html**

Fragment HTMX pour la liste des sources et la modale d'edition (utilise par les routes HTMX de sources.py).

- [ ] **Step 5: Commit**

```bash
git add app/routers/sources.py app/routers/parametres.py app/templates/parametres.html app/templates/partials/source_modale.html
git commit -m "feat: admin sources + parametres — CRUD sources, cles API, thematiques"
```

---

## Task 14 : Router webhooks (n8n)

**Files:**
- Create: `app/routers/webhooks.py`
- Create: `tests/test_router_webhooks.py`

- [ ] **Step 1: Ecrire le test**

```python
# tests/test_router_webhooks.py
"""Tests du router webhooks."""

from app.schemas import WebhookSignal


def test_webhook_signal_validation():
    """Verifie la validation du payload webhook."""
    payload = WebhookSignal(
        source="n8n-linkedin",
        titre="LinkedIn signal test",
        donnees={"test": True},
        token="flz_test_token_123",
    )
    assert payload.source == "n8n-linkedin"
    assert payload.token == "flz_test_token_123"


def test_webhook_signal_token_trop_court():
    """Un token trop court doit echouer la validation."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WebhookSignal(
            source="test",
            titre="Test",
            token="short",
        )
```

- [ ] **Step 2: Lancer les tests**

```bash
docker compose exec app python -m pytest tests/test_router_webhooks.py -v
```

Expected: PASS

- [ ] **Step 3: Implementer webhooks.py**

```python
# app/routers/webhooks.py
"""Endpoint webhook pour recevoir des signaux depuis n8n ou autres."""

from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

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
    # Verifier le token
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

    # Creer la decouverte
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
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/webhooks.py tests/test_router_webhooks.py
git commit -m "feat: endpoint webhook — reception signaux n8n"
```

---

## Task 15 : Navigation + integration main.py

**Files:**
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/routers/niches.py` (route accueil → /analyser)
- Modify: `app/static/css/app.css`

- [ ] **Step 1: Mettre a jour main.py**

```python
"""Point d'entree de l'application Floouzz."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import decouvertes, niches, parametres, sources, webhooks

app = FastAPI(
    title="Floouzz",
    description="Recherche et veille de niches de marche basee sur des signaux multi-sources.",
    version="0.2.0",
)

# Fichiers statiques
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routes
app.include_router(decouvertes.router)
app.include_router(niches.router)
app.include_router(sources.router)
app.include_router(parametres.router)
app.include_router(webhooks.router)
```

- [ ] **Step 2: Modifier la route accueil dans niches.py**

La page d'accueil `/` doit maintenant rediriger vers `/decouverte/`. Modifier le router niches :

- Changer `@router.get("/")` en `@router.get("/analyser")`
- Ajouter une redirection racine :

```python
from fastapi.responses import RedirectResponse

@router.get("/")
async def redirect_accueil():
    """Redirige l'accueil vers le mode Decouverte."""
    return RedirectResponse(url="/decouverte/", status_code=302)
```

- Modifier le template `index.html` : le formulaire `hx-post="/analyser"` reste le meme.
- Ajouter un parametre GET `mot_cle` sur la route `page_accueil` pour le pre-remplissage depuis "Approfondir" :

```python
@router.get("/analyser", response_class=HTMLResponse)
async def page_accueil(
    request: Request,
    mot_cle: str | None = None,
    db: AsyncSession = Depends(get_db),
):
```

Et passer `mot_cle` au template.

- [ ] **Step 3: Mettre a jour base.html — navigation 3 pages**

Remplacer le header dans `app/templates/base.html` :

```html
    <header class="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-50">
        <div class="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
            <a href="/" class="flex items-center gap-2">
                <span class="text-2xl font-bold text-floouzz-500">Floouzz</span>
                <span class="text-xs text-gray-500 mt-1">v0.2</span>
            </a>
            <nav class="flex items-center gap-6">
                <a href="/decouverte/" class="text-sm text-gray-400 hover:text-white transition">Decouverte</a>
                <a href="/analyser" class="text-sm text-gray-400 hover:text-white transition">Analyser</a>
                <a href="/parametres/" class="text-sm text-gray-400 hover:text-white transition">Parametres</a>
            </nav>
        </div>
    </header>
```

- [ ] **Step 4: Verifier que l'app demarre**

```bash
docker compose logs app --tail 5
```

Expected: `Application startup complete.`

Test rapide :

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/decouverte/
```

Expected: `200`

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routers/niches.py app/templates/base.html app/static/css/app.css
git commit -m "feat: navigation 3 pages — Decouverte (accueil), Analyser, Parametres"
```

---

## Task 16 : Sources par defaut + seed data

**Files:**
- Create: `app/services/seed.py`

- [ ] **Step 1: Creer le seed des sources par defaut**

```python
# app/services/seed.py
"""Insertion des sources par defaut au premier demarrage."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = [
    {
        "nom": "Google Trends FR",
        "type": "serpapi",
        "config": {"engine": "google_trends", "gl": "FR", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Jobs FR",
        "type": "serpapi",
        "config": {"engine": "google_jobs", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Search (CPC + PAA)",
        "type": "serpapi",
        "config": {"engine": "google", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News FR",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Reddit",
        "type": "apify_actor",
        "config": {
            "actor_id": "trudax/reddit-scraper",
            "input": {
                "subreddits": ["SaaS", "smallbusiness", "startups", "Entrepreneur", "artificial"],
                "sort": "hot",
                "time": "week",
                "maxItems": 30,
            },
        },
        "cle_api_ref": "APIFY_TOKEN",
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Hacker News",
        "type": "apify_hackernews",
        "config": {},
        "cle_api_ref": None,
        "cron_expr": "0 6 * * *",
    },
]


async def seed_sources_par_defaut(db: AsyncSession) -> int:
    """Insere les sources par defaut si la table est vide."""
    stmt = select(Source).limit(1)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        logger.info("Sources deja presentes — seed ignore")
        return 0

    count = 0
    for s in DEFAULT_SOURCES:
        source = Source(**s)
        db.add(source)
        count += 1

    await db.commit()
    logger.info(f"Seed : {count} sources par defaut inserees")
    return count
```

- [ ] **Step 2: Appeler le seed au demarrage de l'app**

Ajouter dans `app/main.py`, apres la creation de `app` :

```python
from contextlib import asynccontextmanager
from app.database import async_session
from app.services.seed import seed_sources_par_defaut


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Actions au demarrage et a l'arret de l'application."""
    async with async_session() as db:
        await seed_sources_par_defaut(db)
    yield


app = FastAPI(
    title="Floouzz",
    description="Recherche et veille de niches de marche basee sur des signaux multi-sources.",
    version="0.2.0",
    lifespan=lifespan,
)
```

- [ ] **Step 3: Verifier le demarrage**

```bash
docker compose restart app && sleep 3 && docker compose logs app --tail 10
```

Expected: logs contenant `Seed : 6 sources par defaut inserees` (ou `Sources deja presentes` si relance).

- [ ] **Step 4: Commit**

```bash
git add app/services/seed.py app/main.py
git commit -m "feat: seed sources par defaut au demarrage — Google, Reddit, HN"
```

---

## Task 17 : Tests d'integration + verification finale

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Ecrire les tests d'integration**

```python
# tests/test_integration.py
"""Tests d'integration — verification du bon fonctionnement global."""

from app.services.scoring import calculer_score_global
from app.services.sources.base import SourceResult, get_source_fetcher
from app.models import Source, Decouverte, Thematique, Preference, Niche


def test_tous_les_fetchers_enregistres():
    """Verifie que tous les types de sources ont un fetcher."""
    types_attendus = [
        "serpapi", "serpapi_jobs", "serpapi_search", "serpapi_news",
        "apify_actor", "apify_producthunt", "apify_hackernews", "apify_url",
    ]
    for t in types_attendus:
        assert get_source_fetcher(t) is not None, f"Fetcher manquant pour '{t}'"


def test_scoring_compatible_phase1():
    """Verifie que le scoring Phase 2 reste compatible avec les signaux Phase 1."""
    signaux_phase1 = [
        {"source": "google_trends", "score_partiel": 72},
    ]
    result = calculer_score_global(signaux_phase1)
    assert result["score_global"] > 0
    assert result["verdict"] in ("explorer", "watchlist", "abandonner")


def test_tous_les_modeles_importables():
    """Verifie que tous les modeles sont importables."""
    assert Source.__tablename__ == "sources"
    assert Decouverte.__tablename__ == "decouvertes"
    assert Thematique.__tablename__ == "thematiques"
    assert Preference.__tablename__ == "preferences"
    assert Niche.__tablename__ == "niches"
```

- [ ] **Step 2: Lancer tous les tests**

```bash
docker compose exec app python -m pytest tests/ -v
```

Expected: tous les tests PASS

- [ ] **Step 3: Verifier l'app dans le navigateur**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/decouverte/
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/analyser
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/parametres/
```

Expected: 302 (redirect), 200, 200, 200

- [ ] **Step 4: Commit final**

```bash
git add tests/
git commit -m "test: tests d'integration Phase 2 — fetchers, scoring, modeles"
```

- [ ] **Step 5: Mettre a jour la doc**

Ajouter dans `docs/changelog.md` :

```markdown
## [0.2.0] — 2026-04-XX

### Phase 2 — Mode Decouverte + Multi-sources

**Ajoute :**
- Mode Decouverte : dashboard signaux quotidiens avec filtres thematiques
- Sources SerpAPI : Google Trends, Jobs, Search (CPC/PAA), News
- Sources Apify : Reddit, Product Hunt, Hacker News, URL scraper
- Endpoint webhook pour reception signaux n8n
- Service enrichissement Claude API (resume, pertinence, tags)
- Service traduction DeepL FR→EN
- Scanner quotidien (collecte + enrichissement decouple)
- Admin sources configurable (CRUD, test, activation)
- Page parametres (cles API masquees, thematiques, stats scan)
- Scoring multi-sources 4 dimensions (demande, douleur, concurrence, monetisation)
- Seed sources par defaut au demarrage
- Navigation 3 pages (Decouverte, Analyser, Parametres)

**Modifie :**
- Google Trends passe de pytrends a SerpAPI (plus stable)
- Page d'accueil redirige vers le mode Decouverte
- Scoring enrichi avec moyenne par dimension
```

```bash
git add docs/changelog.md
git commit -m "docs: changelog Phase 2"
```

---

## Resume des commits (17 tasks)

| # | Commit | Fichiers principaux |
|---|--------|-------------------|
| 1 | Migration BDD Phase 2 | `migrations/phase2.sql` |
| 2 | Modeles + schemas | `app/models.py`, `app/schemas.py` |
| 3 | Config cles API | `app/config.py`, `requirements.txt` |
| 4 | Interface sources base.py | `app/services/sources/base.py` |
| 5 | Google Trends SerpAPI | `app/services/sources/google_trends.py` |
| 6 | Google Jobs/Search/News | `app/services/sources/google_*.py` |
| 7 | Reddit/PH/HN/URL Apify | `app/services/sources/reddit.py`, etc. |
| 8 | Traduction DeepL | `app/services/traduction.py` |
| 9 | Enrichissement Claude | `app/services/enrichissement.py` |
| 10 | Scanner orchestrateur | `app/services/scanner.py` |
| 11 | Scoring multi-sources | `app/services/scoring.py` |
| 12 | Dashboard Decouverte | `app/routers/decouvertes.py`, templates |
| 13 | Admin sources + parametres | `app/routers/sources.py`, `parametres.py` |
| 14 | Webhook n8n | `app/routers/webhooks.py` |
| 15 | Navigation + integration | `app/main.py`, `base.html` |
| 16 | Seed sources defaut | `app/services/seed.py` |
| 17 | Tests integration + doc | `tests/`, `docs/changelog.md` |
