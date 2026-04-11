"""Fixtures pytest pour les tests Floouzz.

Strategie : fake DB session scriptable + mocks cibles sur la route /analyser.
Zero dependance a une vraie BDD PostgreSQL — tests rapides et isoles.

Les tests asynchrones doivent utiliser @pytest.mark.asyncio (mode strict par
defaut de pytest-asyncio 0.25).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

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

    def fetchall(self):
        """Alias de all() pour compat avec les requetes text() SQLAlchemy."""
        return list(self._data)


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

    async def execute(self, stmt, params=None):
        """
        Accepte le stmt et des params optionnels (pour les requetes text()
        parametrees comme celles de pipeline_ia._lire_cache/_ecrire_cache).
        """
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
def mock_serp_gap(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches.analyser_serp.
    Retourne un resultat serp_gap factice conforme au contrat pour que les
    tests de /analyser n'appellent jamais le vrai SerpAPI ni Claude.
    """
    mock = AsyncMock(return_value={
        "mot_cle": "test",
        "score_difficulte": 3,
        "verdict": "FACILE",
        "verdict_raison": "Raison de test.",
        "opportunites": ["Piste 1", "Piste 2"],
        "faiblesses_detectees": ["Faiblesse 1"],
        "top_10": [],
    })
    monkeypatch.setattr("app.routers.niches.analyser_serp", mock)
    return mock


@pytest.fixture
def mock_affiliate_finder(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches.chercher_affiliation.
    Retourne un resultat affiliate_finder factice conforme au contrat pour
    que les tests de /analyser n'appellent jamais le vrai SerpAPI ni Claude.
    """
    mock = AsyncMock(return_value={
        "mot_cle": "test",
        "score_affiliation": 4,
        "verdict": "FAIBLE",
        "verdict_raison": "Raison affiliation de test.",
        "plateformes_detectees": ["Amazon Associates"],
        "programmes": [
            {
                "nom": "Programme test",
                "plateforme": "Amazon Associates",
                "commission": "5%",
                "cookie_duree": "24h",
                "source_url": "https://example.com",
            },
        ],
        "opportunites": ["Opportunite affiliation 1"],
        "requetes_utilisees": [
            '"test" programme affiliation',
            "test amazon associates OR awin",
        ],
        "nb_resultats_analyses": 10,
    })
    monkeypatch.setattr("app.routers.niches.chercher_affiliation", mock)
    return mock


@pytest.fixture
def mock_saisonnalite(monkeypatch) -> AsyncMock:
    """
    Patch app.routers.niches.analyser_saisonnalite.
    Retourne un resultat saisonnalite factice conforme au contrat pour que
    les tests de /analyser n'appellent jamais le vrai SerpAPI Google Trends.
    """
    mock = AsyncMock(return_value={
        "mot_cle": "test",
        "periode": "12 derniers mois",
        "geo": "FR",
        "score_saisonnalite": 4,
        "verdict": "CYCLIQUE",
        "verdict_raison": "Cycles moderes : ratio pic/moyenne = 2.5.",
        "stats": {
            "nb_points": 53,
            "min": 20,
            "max": 100,
            "moyenne": 45.0,
            "mediane": 42.0,
            "ecart_type": 18.0,
            "ratio_pic_moyenne": 2.5,
            "coefficient_variation": 0.4,
            "concentration_top8": 0.30,
        },
        "pic": {
            "mois_principal": "janvier",
            "date_pic": "11-17 janv. 2026",
            "valeur_pic": 100,
            "semaines_top_80pct": 20,
        },
        "position_actuelle": {
            "mois_actuel": "avril",
            "distance_au_pic_mois": 9,
            "phase": "creux",
        },
        "recommandations": [
            "Creux actuel, pic attendu en janvier (dans 9 mois).",
            "Lance ton contenu 8 mois avant pour etre indexe au bon moment.",
        ],
        "serie": {
            "1-7 avr. 2025": 45,
            "11-17 janv. 2026": 100,
        },
    })
    monkeypatch.setattr("app.routers.niches.analyser_saisonnalite", mock)
    return mock


@pytest.fixture
def mocks_analyser(
    mock_pipeline_ia: AsyncMock,
    mock_google_trends: AsyncMock,
    mock_thematiques: AsyncMock,
    mock_serp_gap: AsyncMock,
    mock_affiliate_finder: AsyncMock,
    mock_saisonnalite: AsyncMock,
) -> dict[str, AsyncMock]:
    """Regroupe les 6 mocks necessaires a un test du POST /analyser."""
    return {
        "pipeline_ia": mock_pipeline_ia,
        "google_trends": mock_google_trends,
        "thematiques": mock_thematiques,
        "serp_gap": mock_serp_gap,
        "affiliate_finder": mock_affiliate_finder,
        "saisonnalite": mock_saisonnalite,
    }


# ---------------------------------------------------------------------------
# Helpers et fixtures pour les tests de services Claude (pipeline_ia, traduction)
# ---------------------------------------------------------------------------

def make_claude_response(text: str) -> MagicMock:
    """
    Construit un mock de reponse Claude (objet Message) avec un seul bloc texte.

    Imite la structure retournee par anthropic.AsyncAnthropic().messages.create() :
    message.content[0].text -> le texte brut de la reponse.

    Utile pour les tests qui mockent l'API Claude sans appel reseau.
    """
    bloc = MagicMock()
    bloc.text = text
    message = MagicMock()
    message.content = [bloc]
    return message


def make_anthropic_client(reponse_text: str | None = None) -> MagicMock:
    """
    Construit un mock d'AsyncAnthropic dont .messages.create est un AsyncMock
    qui retourne make_claude_response(reponse_text).

    Si reponse_text est None, create retourne un message vide (liste content vide).
    """
    client = MagicMock()
    if reponse_text is None:
        message = MagicMock()
        message.content = []
    else:
        message = make_claude_response(reponse_text)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=message)
    return client


@pytest.fixture
def mock_anthropic_pipeline(monkeypatch):
    """
    Patche anthropic.AsyncAnthropic pour les tests de pipeline_ia.

    Retourne une factory : appeler `mock_anthropic_pipeline(reponse_text)`
    pour configurer ce que Claude renverra au prochain appel messages.create.
    Le client est patche dans le namespace de app.services.pipeline_ia.
    """
    def _install(reponse_text: str | None):
        client = make_anthropic_client(reponse_text)
        fake_factory = MagicMock(return_value=client)
        monkeypatch.setattr(
            "app.services.pipeline_ia.anthropic.AsyncAnthropic", fake_factory
        )
        return client

    return _install


@pytest.fixture
def mock_anthropic_traduction(monkeypatch):
    """
    Patche anthropic.AsyncAnthropic pour les tests de traduction.
    Meme pattern que mock_anthropic_pipeline mais sur le namespace traduction.
    """
    def _install(reponse_text: str | None):
        client = make_anthropic_client(reponse_text)
        fake_factory = MagicMock(return_value=client)
        monkeypatch.setattr(
            "app.services.traduction.anthropic.AsyncAnthropic", fake_factory
        )
        return client

    return _install


@pytest.fixture
def mock_anthropic_raise_pipeline(monkeypatch):
    """
    Patche anthropic.AsyncAnthropic dans pipeline_ia pour lever une exception
    au prochain appel messages.create. Utile pour tester les fallbacks reseau.
    """
    def _install(exc: Exception):
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=exc)
        fake_factory = MagicMock(return_value=client)
        monkeypatch.setattr(
            "app.services.pipeline_ia.anthropic.AsyncAnthropic", fake_factory
        )
        return client

    return _install


@pytest.fixture
def mock_anthropic_raise_traduction(monkeypatch):
    """Variant de mock_anthropic_raise pour le namespace traduction."""
    def _install(exc: Exception):
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=exc)
        fake_factory = MagicMock(return_value=client)
        monkeypatch.setattr(
            "app.services.traduction.anthropic.AsyncAnthropic", fake_factory
        )
        return client

    return _install


@pytest.fixture
def anthropic_key(monkeypatch):
    """
    Force settings.ANTHROPIC_API_KEY a une valeur de test pour les tests
    qui veulent traverser le 'if not settings.ANTHROPIC_API_KEY' guard.

    Sans cette fixture, les tests verraient la valeur du .env local
    (potentiellement vraie cle API — ne pas l'utiliser pour les vrais appels).
    """
    monkeypatch.setattr("app.services.pipeline_ia.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.services.traduction.settings.ANTHROPIC_API_KEY", "test-key")
    return "test-key"


@pytest.fixture
def no_anthropic_key(monkeypatch):
    """
    Force settings.ANTHROPIC_API_KEY a None dans les deux modules.
    Pour tester les fallbacks quand la cle API est absente.
    """
    monkeypatch.setattr("app.services.pipeline_ia.settings.ANTHROPIC_API_KEY", None)
    monkeypatch.setattr("app.services.traduction.settings.ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# Assertion helper : valide la structure d'un resultat pipeline_ia
# ---------------------------------------------------------------------------

def assert_resultat_pipeline_valide(resultat: dict) -> None:
    """
    Verifie qu'un dict retourne par pipeline_ia.analyser() respecte le contrat :
    - cles obligatoires presentes
    - scores dict avec les 4 dimensions
    - types corrects
    - bornes respectees (scores 0-10, verdict GO/WATCH/SKIP)
    """
    assert isinstance(resultat, dict)

    cles_obligatoires = {
        "resume_fr", "tags", "niche_detectee", "scores", "score_global",
        "verdict", "verdict_raison", "mots_cles_seo", "risque_ymyl",
    }
    assert cles_obligatoires.issubset(resultat.keys()), (
        f"Cles manquantes : {cles_obligatoires - resultat.keys()}"
    )

    assert isinstance(resultat["resume_fr"], str)
    assert isinstance(resultat["tags"], list)
    assert isinstance(resultat["mots_cles_seo"], list)
    assert isinstance(resultat["risque_ymyl"], bool)
    assert resultat["verdict"] in ("GO", "WATCH", "SKIP")

    assert isinstance(resultat["score_global"], int)
    assert 0 <= resultat["score_global"] <= 10

    assert isinstance(resultat["scores"], dict)
    for dim in ("demande", "douleur", "concurrence", "monetisation"):
        assert dim in resultat["scores"], f"Dimension manquante : {dim}"
        bloc = resultat["scores"][dim]
        assert "valeur" in bloc and "justification" in bloc
        assert 0 <= bloc["valeur"] <= 10
        assert isinstance(bloc["justification"], str)


# ---------------------------------------------------------------------------
# Fixture : FakeDbSession qui simule une table cache_ia absente
# ---------------------------------------------------------------------------

class FakeDbSessionCacheAbsent(FakeDbSession):
    """
    Variante de FakeDbSession dont execute() leve une exception pour toute
    requete sur cache_ia. Permet de tester la robustesse de pipeline_ia quand
    la table n'existe pas (rejeu partiel des migrations, BDD non initialisee...).

    Les requetes SQLAlchemy classiques (select/insert sur d'autres tables)
    continuent de fonctionner normalement via le script queue_result().
    """

    async def execute(self, stmt, params=None):
        # Detecte les requetes text() qui mentionnent cache_ia
        stmt_str = str(stmt).lower()
        if "cache_ia" in stmt_str:
            raise RuntimeError("relation cache_ia does not exist")
        return await super().execute(stmt, params)


@pytest.fixture
def fake_db_cache_absent() -> "FakeDbSessionCacheAbsent":
    """Fake session qui raise sur toute requete cache_ia."""
    return FakeDbSessionCacheAbsent()
