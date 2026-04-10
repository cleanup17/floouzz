"""Fixtures pytest pour les tests Floouzz.

Strategie : fake DB session scriptable + mocks cibles sur la route /analyser.
Zero dependance a une vraie BDD PostgreSQL — tests rapides et isoles.

Les tests asynchrones doivent utiliser @pytest.mark.asyncio (mode strict par
defaut de pytest-asyncio 0.25).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.models import Analyse


# ---------------------------------------------------------------------------
# Helper : resultat factice au format pipeline_ia
# ---------------------------------------------------------------------------

def make_pipeline_result(
    score_global: int = 7,
    verdict: str = "GO",
    resume_fr: str = "Resume de test.",
    niche_detectee: str | None = "test-niche",
    risque_ymyl: bool = False,
) -> dict[str, Any]:
    """Produit un dict conforme au contrat pipeline_ia.analyser pour les tests."""
    return {
        "resume_fr": resume_fr,
        "tags": ["IA", "SaaS"],
        "niche_detectee": niche_detectee,
        "scores": {
            "demande":      {"valeur": 8, "justification": "Forte demande."},
            "douleur":      {"valeur": 7, "justification": "Douleur reelle."},
            "concurrence":  {"valeur": 6, "justification": "Concurrence moyenne."},
            "monetisation": {"valeur": 7, "justification": "Monetisable."},
        },
        "score_global": score_global,
        "verdict": verdict,
        "verdict_raison": "Raison du verdict de test.",
        "mots_cles_seo": ["test", "niche", "marche"],
        "risque_ymyl": risque_ymyl,
    }


# ---------------------------------------------------------------------------
# Fake session DB — capture les add() et sert des resultats scriptes
# ---------------------------------------------------------------------------

class FakeResult:
    """Resultat minimaliste pour FakeDbSession.execute()."""

    def __init__(self, data: list | None = None):
        self._data = list(data or [])

    def scalar_one_or_none(self):
        return self._data[0] if self._data else None

    def scalar_one(self):
        if not self._data:
            raise ValueError("FakeResult vide pour scalar_one()")
        return self._data[0]

    def scalar(self):
        return self._data[0] if self._data else None

    def scalars(self):
        # Retourne self : notre .all() et .first() se comportent pareil
        return self

    def all(self):
        return list(self._data)

    def first(self):
        return self._data[0] if self._data else None


class FakeDbSession:
    """
    Fake session async qui :
    - capture les entites ajoutees via .add()
    - attribue un UUID aux entites flushees
    - sert des FakeResult scriptes a chaque .execute()
    - accepte un lambda dans le script (evalue a l'execute-time) pour les
      cas ou on a besoin de reference une entite ajoutee plus tot dans le flux
    """

    def __init__(self):
        self.added: list = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self._script: list = []

    # -- API utilisee par les tests pour scripter le comportement ----------

    def queue_result(self, result_or_callable) -> None:
        """
        Ajoute un resultat a la fin du script execute().

        Accepte un FakeResult ou un callable(session) -> FakeResult pour les
        cas ou le resultat depend d'une entite ajoutee plus tot dans le flux
        (ex: reload d'une Analyse qu'on vient de creer).
        """
        self._script.append(result_or_callable)

    def added_of_type(self, cls) -> list:
        """Retourne les objets ajoutes d'un type donne."""
        return [o for o in self.added if isinstance(o, cls)]

    def last_added(self, cls):
        """Retourne le dernier objet ajoute d'un type donne, ou None."""
        objs = self.added_of_type(cls)
        return objs[-1] if objs else None

    # -- API utilisee par SQLAlchemy / FastAPI -----------------------------

    async def execute(self, stmt):
        if not self._script:
            return FakeResult([])
        item = self._script.pop(0)
        if callable(item) and not isinstance(item, FakeResult):
            return item(self)
        return item

    def add(self, obj) -> None:
        self.added.append(obj)
        # Assigne un UUID si le modele en attend un et n'en a pas
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        # created_at par defaut si absent
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)

    async def flush(self) -> None:
        self.flushes += 1
        for obj in self.added:
            if hasattr(obj, "id") and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helper : script par defaut pour le flux POST /analyser
# ---------------------------------------------------------------------------

def script_flux_analyser_nouvelle_niche(fake_db: FakeDbSession) -> None:
    """
    Configure le script execute() pour un POST /analyser sur une niche qui
    n'existe pas encore en base.

    Sequence des appels execute() declenches par la route :
      1. Lookup Niche par mot_cle  -> aucun resultat (nouvelle niche)
      2. Reload Analyse + signaux  -> la derniere Analyse ajoutee
      3. Count analyses            -> 1
    """
    fake_db.queue_result(FakeResult([]))
    fake_db.queue_result(lambda s: FakeResult([s.last_added(Analyse)]))
    fake_db.queue_result(FakeResult([1]))


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_db() -> FakeDbSession:
    """Fake session vide, scriptable via .queue_result()."""
    return FakeDbSession()


@pytest_asyncio.fixture
async def app_client(fake_db: FakeDbSession):
    """
    AsyncClient httpx branche sur l'app FastAPI via ASGITransport.
    Override get_db pour injecter la fake session pendant la duree du test.
    """

    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def mock_pipeline_ia(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches.analyser_pipeline.
    Retourne l'AsyncMock pour que les tests puissent inspecter les arguments
    ou configurer return_value selon le scenario.
    """
    mock = AsyncMock(return_value=make_pipeline_result())
    monkeypatch.setattr("app.routers.niches.analyser_pipeline", mock)
    return mock


@pytest.fixture
def mock_google_trends(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches.fetch_google_trends.
    Par defaut retourne un signal factice en hausse a +18.3%.
    """
    mock = AsyncMock(return_value={
        "donnees": {
            "mot_cle": "test",
            "moyenne": 62.5,
            "tendance": "hausse",
            "variation_pct": 18.3,
        },
        "score_partiel": 70,
    })
    monkeypatch.setattr("app.routers.niches.fetch_google_trends", mock)
    return mock


@pytest.fixture
def mock_thematiques(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches._charger_thematiques_actives pour bypasser
    l'appel DB aux thematiques.
    """
    mock = AsyncMock(return_value=["IA", "SaaS", "Marketing"])
    monkeypatch.setattr("app.routers.niches._charger_thematiques_actives", mock)
    return mock


@pytest.fixture
def mocks_analyser(
    mock_pipeline_ia: AsyncMock,
    mock_google_trends: AsyncMock,
    mock_thematiques: AsyncMock,
) -> dict[str, AsyncMock]:
    """Regroupe les 3 mocks necessaires a un test du POST /analyser."""
    return {
        "pipeline_ia": mock_pipeline_ia,
        "google_trends": mock_google_trends,
        "thematiques": mock_thematiques,
    }
