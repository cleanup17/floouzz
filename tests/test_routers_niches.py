"""
Tests du router /analyser.

Strategie : fake DB session + mocks sur pipeline_ia.analyser, fetch_google_trends
et _charger_thematiques_actives. Les assertions portent sur les arguments passes
aux mocks et sur l'etat de la fake session (entites ajoutees), pas sur le HTML
rendu qui est considere comme un detail d'implementation.
"""

import pytest

from app.models import Analyse, Niche, Signal
from tests.conftest import (
    FakeResult,
    make_pipeline_result,
    script_flux_analyser_nouvelle_niche,
)


# ---------------------------------------------------------------------------
# Cas d'erreur : validation du mot-cle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_mot_cle_vide_retourne_erreur(app_client, fake_db, mocks_analyser):
    """Un POST /analyser avec mot_cle vide doit rendre le template erreur
    et ne pas appeler pipeline_ia ni creer d'Analyse."""
    response = await app_client.post("/analyser", data={"mot_cle": ""})

    assert response.status_code == 200
    assert "2 caracteres" in response.text
    mocks_analyser["pipeline_ia"].assert_not_awaited()
    mocks_analyser["google_trends"].assert_not_awaited()
    assert fake_db.added_of_type(Analyse) == []
    assert fake_db.added_of_type(Niche) == []


@pytest.mark.asyncio
async def test_analyser_mot_cle_trop_court(app_client, fake_db, mocks_analyser):
    """Un mot_cle d'un seul caractere doit etre rejete."""
    response = await app_client.post("/analyser", data={"mot_cle": "a"})

    assert response.status_code == 200
    assert "2 caracteres" in response.text
    mocks_analyser["pipeline_ia"].assert_not_awaited()
    assert fake_db.commits == 0


# ---------------------------------------------------------------------------
# Flux nominal : creation d'une nouvelle niche + analyse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_cree_niche_et_analyse(app_client, fake_db, mocks_analyser):
    """
    Flux nominal : une nouvelle niche est creee avec une Analyse portant
    tous les champs du format pipeline_ia (scores 0-10, verdict GO/WATCH/SKIP,
    resume_fr, tags, mots_cles_seo, risque_ymyl, pipeline_ia JSONB brut).
    """
    script_flux_analyser_nouvelle_niche(fake_db)

    response = await app_client.post("/analyser", data={"mot_cle": "automatisation comptable"})

    assert response.status_code == 200

    # pipeline_ia a ete appele une fois avec les bons kwargs
    mocks_analyser["pipeline_ia"].assert_awaited_once()
    kwargs = mocks_analyser["pipeline_ia"].await_args.kwargs
    assert kwargs["titre"] == "automatisation comptable"
    assert kwargs["source"] == "analyse"
    assert kwargs["thematiques"] == ["IA", "SaaS", "Marketing"]
    assert kwargs["session"] is fake_db

    # Niche creee en base
    niches = fake_db.added_of_type(Niche)
    assert len(niches) == 1
    assert niches[0].mot_cle == "automatisation comptable"

    # Analyse creee avec le format pipeline_ia
    analyses = fake_db.added_of_type(Analyse)
    assert len(analyses) == 1
    analyse = analyses[0]

    assert analyse.score_global == 7
    assert analyse.score_demande == 8
    assert analyse.score_douleur == 7
    assert analyse.score_concurrence == 6
    assert analyse.score_monetisation == 7
    assert analyse.verdict == "GO"
    assert analyse.verdict_raison == "Raison du verdict de test."
    assert analyse.resume_fr == "Resume de test."
    assert analyse.tags == ["IA", "SaaS"]
    assert analyse.mots_cles_seo == ["test", "niche", "marche"]
    assert analyse.risque_ymyl is False
    assert analyse.niche_detectee == "test-niche"
    # Le JSONB brut doit contenir tout le resultat pipeline_ia
    assert analyse.pipeline_ia is not None
    assert analyse.pipeline_ia["verdict"] == "GO"
    assert "scores" in analyse.pipeline_ia

    # Un signal Google Trends a ete persiste
    signaux = fake_db.added_of_type(Signal)
    assert len(signaux) == 1
    assert signaux[0].source == "google_trends"
    assert signaux[0].score_partiel == 70

    # Commit effectue
    assert fake_db.commits == 1


# ---------------------------------------------------------------------------
# Format de la synthese injectee dans pipeline_ia
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_construit_synthese_format_fixe(
    app_client, fake_db, mocks_analyser,
):
    """
    Le contenu passe a pipeline_ia doit respecter le format fixe :
    "{mot_cle} — Tendance : {variation}%. Interet : {description}.
     Sources : {nb} signaux collectes."
    """
    script_flux_analyser_nouvelle_niche(fake_db)

    # On force la reponse Google Trends pour controler les valeurs
    mocks_analyser["google_trends"].return_value = {
        "donnees": {
            "mot_cle": "chatbot vocal",
            "moyenne": 55.0,
            "tendance": "hausse",
            "variation_pct": 42.5,
        },
        "score_partiel": 75,
    }

    await app_client.post("/analyser", data={"mot_cle": "chatbot vocal"})

    kwargs = mocks_analyser["pipeline_ia"].await_args.kwargs
    contenu = kwargs["contenu"]

    assert "chatbot vocal" in contenu
    assert "Tendance : 42.5%" in contenu
    assert "hausse" in contenu
    assert "moyenne 55" in contenu
    assert "1 signaux collectes" in contenu


# ---------------------------------------------------------------------------
# Passage des donnees signaux a pipeline_ia
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_passe_signaux_a_pipeline_ia(
    app_client, fake_db, mocks_analyser,
):
    """Les signaux bruts collectes doivent etre transmis a pipeline_ia
    via le parametre donnees (pour contexte / debug)."""
    script_flux_analyser_nouvelle_niche(fake_db)

    await app_client.post("/analyser", data={"mot_cle": "micro saas"})

    kwargs = mocks_analyser["pipeline_ia"].await_args.kwargs
    donnees = kwargs["donnees"]

    assert "signaux" in donnees
    assert len(donnees["signaux"]) == 1
    assert donnees["signaux"][0]["source"] == "google_trends"
    assert donnees["signaux"][0]["score_partiel"] == 70


# ---------------------------------------------------------------------------
# Verdicts alternatifs — WATCH et SKIP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict,score",
    [("GO", 8), ("WATCH", 5), ("SKIP", 2)],
)
async def test_analyser_enregistre_tous_les_verdicts(
    app_client, fake_db, mocks_analyser, verdict, score,
):
    """Les 3 verdicts GO/WATCH/SKIP doivent tous etre persistes correctement."""
    script_flux_analyser_nouvelle_niche(fake_db)
    mocks_analyser["pipeline_ia"].return_value = make_pipeline_result(
        score_global=score, verdict=verdict,
    )

    await app_client.post("/analyser", data={"mot_cle": "niche test"})

    analyse = fake_db.last_added(Analyse)
    assert analyse is not None
    assert analyse.verdict == verdict
    assert analyse.score_global == score


# ---------------------------------------------------------------------------
# Risque YMYL propage depuis pipeline_ia
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_propage_risque_ymyl(app_client, fake_db, mocks_analyser):
    """Le flag risque_ymyl retourne par pipeline_ia doit etre persiste."""
    script_flux_analyser_nouvelle_niche(fake_db)
    mocks_analyser["pipeline_ia"].return_value = make_pipeline_result(
        risque_ymyl=True,
    )

    await app_client.post("/analyser", data={"mot_cle": "conseil juridique ia"})

    analyse = fake_db.last_added(Analyse)
    assert analyse is not None
    assert analyse.risque_ymyl is True


# ---------------------------------------------------------------------------
# Reutilisation d'une niche existante
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyser_reutilise_niche_existante(
    app_client, fake_db, mocks_analyser,
):
    """
    Si la niche existe deja, on ne doit pas en creer une nouvelle.
    Une nouvelle Analyse est creee et rattachee a la niche existante.
    """
    niche_existante = Niche(mot_cle="niche connue")
    import uuid as _uuid
    niche_existante.id = _uuid.uuid4()

    # Script : lookup trouve la niche, puis reload analyse, puis count
    fake_db.queue_result(FakeResult([niche_existante]))
    fake_db.queue_result(lambda s: FakeResult([s.last_added(Analyse)]))
    fake_db.queue_result(FakeResult([2]))

    await app_client.post("/analyser", data={"mot_cle": "niche connue"})

    # Pas de nouvelle niche creee
    niches_ajoutees = fake_db.added_of_type(Niche)
    assert niches_ajoutees == []

    # Une analyse creee et rattachee a la niche existante
    analyse = fake_db.last_added(Analyse)
    assert analyse is not None
    assert analyse.niche_id == niche_existante.id
