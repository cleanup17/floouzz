"""
Tests du service app.services.deduplication.

Couvre :
- _normaliser()            : lowercase + strip + None safe
- _similarite_titre()      : ratio difflib
- chercher_doublon()       : 3 criteres par ordre de priorite :
    1. niche_detectee exact (case insensitive, SQL)
    2. >= 2 tags en commun
    3. similarite titre > 0.80

Strategie de mock : on script FakeDbSession.execute() pour rendre les
3 requetes SQL successives. Ordre des execute() :
    1. SELECT niche_detectee (si niche_detectee fourni)
    2. SELECT tags && (si tags non vide)
    3. SELECT LIKE titre (si candidats < 50)
Un test peut sauter une requete en passant niche_detectee=None ou tags=[].
"""

import uuid

import pytest

from app.services.deduplication import (
    NB_TAGS_COMMUNS_MIN,
    SEUIL_SIMILARITE_TITRE,
    _normaliser,
    _similarite_titre,
    chercher_doublon,
)
from tests.conftest import FakeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_row(id_uuid: uuid.UUID, titre: str, tags: list[str] | None = None) -> tuple:
    """
    Construit une ligne (id, titre, tags) comme la retournerait PostgreSQL
    via les requetes text() de deduplication.
    """
    return (id_uuid, titre, tags or [])


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# _normaliser — fonction pure
# ---------------------------------------------------------------------------

class TestNormaliser:
    def test_lowercase(self):
        assert _normaliser("SaaS") == "saas"

    def test_strip_whitespace(self):
        assert _normaliser("  IA  ") == "ia"

    def test_none_retourne_vide(self):
        assert _normaliser(None) == ""

    def test_vide_retourne_vide(self):
        assert _normaliser("") == ""

    def test_combine_lowercase_et_strip(self):
        assert _normaliser("  MarKeTinG\n") == "marketing"


# ---------------------------------------------------------------------------
# _similarite_titre — fonction pure
# ---------------------------------------------------------------------------

class TestSimilariteTitre:
    def test_identique_ratio_1(self):
        assert _similarite_titre("SaaS pour comptable", "SaaS pour comptable") == 1.0

    def test_different_ratio_faible(self):
        """Deux titres sans rapport -> ratio bas."""
        ratio = _similarite_titre("SaaS comptable", "Recette cuisine")
        assert ratio < 0.4

    def test_insensible_casse(self):
        """Casse n'influe pas (normalisation en amont)."""
        r1 = _similarite_titre("SaaS POUR Comptable", "saas pour comptable")
        assert r1 == 1.0

    def test_quasi_identique_au_dessus_seuil(self):
        """Deux titres a 85% devraient depasser 0.80."""
        ratio = _similarite_titre(
            "SaaS automatisation comptable pour TPE",
            "SaaS automatisation comptable des TPE",
        )
        assert ratio > SEUIL_SIMILARITE_TITRE


# ---------------------------------------------------------------------------
# chercher_doublon — critere 1 : niche_detectee exact
# ---------------------------------------------------------------------------

class TestCritere1NicheDetectee:

    @pytest.mark.asyncio
    async def test_doublon_par_niche_exact(self, fake_db):
        """Niche detectee identique -> retourne l'UUID du signal existant."""
        existing_id = new_uuid()
        fake_db.queue_result(FakeResult([(existing_id,)]))  # SELECT niche

        result = await chercher_doublon(
            fake_db,
            titre="Nouveau titre",
            tags=["IA"],
            niche_detectee="automatisation comptable",
        )

        assert result == existing_id

    @pytest.mark.asyncio
    async def test_doublon_par_niche_case_insensitive(self, fake_db):
        """
        Le SQL fait LOWER() des deux cotes donc la casse est ignoree.
        On simule le retour positif et verifie qu'il est utilise.
        """
        existing_id = new_uuid()
        fake_db.queue_result(FakeResult([(existing_id,)]))

        result = await chercher_doublon(
            fake_db,
            titre="Titre",
            tags=[],
            niche_detectee="AUTOMATISATION COMPTABLE",
        )

        assert result == existing_id

    @pytest.mark.asyncio
    async def test_pas_de_niche_critere_1_skippe(self, fake_db):
        """
        niche_detectee=None -> on saute le critere 1, pas de requete niche.
        Sans tags non plus -> pas de critere 2 -> return None direct.
        """
        # Aucune requete n'est scriptee : le seul execute() possible serait
        # le critere 3 si titre non vide, mais meme la le pre-filtre tags
        # est absent et candidats reste vide.
        # Note : avec titre non vide + tags=[] + niche=None, le code passe
        # dans le bloc "elargissement par prefixe titre"
        fake_db.queue_result(FakeResult([]))  # SELECT LIKE prefixe titre

        result = await chercher_doublon(
            fake_db, titre="Titre quelconque", tags=[], niche_detectee=None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_niche_sql_echoue_continue_autres_criteres(self, fake_db):
        """
        Exception sur la requete niche -> on continue sur les criteres 2/3.
        On script une requete qui raise pour la niche, puis un retour vide
        pour les candidats -> return None.
        """
        # Lambda qui raise pour simuler l'echec du SELECT niche
        def raise_on_niche(s):
            raise RuntimeError("colonne niche_detectee inconnue")

        fake_db.queue_result(raise_on_niche)                # niche raise
        fake_db.queue_result(FakeResult([]))                # tags && vide
        fake_db.queue_result(FakeResult([]))                # LIKE titre vide

        result = await chercher_doublon(
            fake_db,
            titre="titre",
            tags=["IA"],
            niche_detectee="niche test",
        )

        assert result is None


# ---------------------------------------------------------------------------
# chercher_doublon — critere 2 : tags communs >= 2
# ---------------------------------------------------------------------------

class TestCritere2TagsCommuns:

    @pytest.mark.asyncio
    async def test_doublon_par_2_tags_communs(self, fake_db):
        """2 tags en commun -> doublon detecte via critere 2."""
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))  # SELECT niche vide
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Autre titre", ["IA", "SaaS", "Marketing"]),
        ]))  # SELECT tags && -> 1 candidat avec 3 tags
        # Pas besoin de scripter LIKE : la boucle trouve avant

        result = await chercher_doublon(
            fake_db,
            titre="Mon titre",
            tags=["IA", "SaaS"],
            niche_detectee="niche differente",
        )

        assert result == cand_id

    @pytest.mark.asyncio
    async def test_1_seul_tag_commun_pas_de_doublon_critere_2(self, fake_db):
        """
        1 seul tag commun -> pas doublon critere 2.
        On continue sur critere 3 (similarite titre) qui doit aussi echouer
        pour retourner None.
        """
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))  # SELECT niche
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Titre completement different", ["IA"]),
        ]))  # SELECT tags -> 1 candidat avec 1 tag commun
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Titre completement different", ["IA"]),
        ]))  # SELECT LIKE prefixe titre (elargissement)

        result = await chercher_doublon(
            fake_db,
            titre="Mon titre original",
            tags=["IA", "SaaS"],
            niche_detectee=None,
        )

        # Pas de match : 1 tag commun < seuil 2, titres divergents
        assert result is None

    @pytest.mark.asyncio
    async def test_tags_communs_case_insensitive(self, fake_db):
        """La comparaison des tags est case-insensitive via _normaliser."""
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "X", ["ia", "saas"]),
        ]))

        result = await chercher_doublon(
            fake_db,
            titre="X",
            tags=["IA", "SaaS"],  # majuscules
            niche_detectee=None,
        )

        assert result == cand_id

    @pytest.mark.asyncio
    async def test_seuil_exact_2_tags_declanche_doublon(self, fake_db):
        """Exactement NB_TAGS_COMMUNS_MIN tags -> doublon (seuil inclusif)."""
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "T", ["IA", "SaaS", "tag_different"]),
        ]))

        result = await chercher_doublon(
            fake_db,
            titre="X",
            tags=["IA", "SaaS"],
            niche_detectee=None,
        )
        assert result == cand_id
        assert NB_TAGS_COMMUNS_MIN == 2  # sanity check


# ---------------------------------------------------------------------------
# chercher_doublon — critere 3 : similarite titre > 0.80
# ---------------------------------------------------------------------------

class TestCritere3SimilariteTitre:

    @pytest.mark.asyncio
    async def test_doublon_par_similarite_titre(self, fake_db):
        """2 titres a ~85% -> doublon via critere 3."""
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "SaaS automatisation comptable pour TPE", ["autre"]),
        ]))
        fake_db.queue_result(FakeResult([]))  # LIKE prefixe vide

        result = await chercher_doublon(
            fake_db,
            titre="SaaS automatisation comptable des TPE",
            tags=["tag_sans_intersection"],
            niche_detectee=None,
        )

        assert result == cand_id

    @pytest.mark.asyncio
    async def test_pas_de_doublon_titres_divergents(self, fake_db):
        """Titres trop differents -> None."""
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Recette crepe suzette", ["autre"]),
        ]))
        fake_db.queue_result(FakeResult([]))  # LIKE prefixe

        result = await chercher_doublon(
            fake_db,
            titre="SaaS gestion de projet",
            tags=["tag_different"],
            niche_detectee=None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_elargissement_par_prefixe_titre(self, fake_db):
        """
        Quand candidats via tags < 50, on elargit via LIKE prefixe.
        Le candidat trouve par LIKE doit aussi etre evalue pour le critere 3.

        niche_detectee=None -> pas de requete niche, donc seulement 2 queues :
        tags && (vide) puis LIKE prefixe (1 candidat).
        """
        cand_id = new_uuid()
        fake_db.queue_result(FakeResult([]))  # tags && (vide -> elargissement)
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "SaaS automatisation comptable pour TPE", []),
        ]))  # LIKE prefixe -> trouve un candidat similaire

        result = await chercher_doublon(
            fake_db,
            titre="SaaS automatisation comptable des TPE",
            tags=["IA"],
            niche_detectee=None,
        )

        assert result == cand_id


# ---------------------------------------------------------------------------
# chercher_doublon — flux d'erreur / bords
# ---------------------------------------------------------------------------

class TestFluxErreur:

    @pytest.mark.asyncio
    async def test_aucun_candidat_retourne_none(self, fake_db):
        """Toutes les requetes retournent vide -> None."""
        fake_db.queue_result(FakeResult([]))  # niche
        fake_db.queue_result(FakeResult([]))  # tags
        fake_db.queue_result(FakeResult([]))  # LIKE titre

        result = await chercher_doublon(
            fake_db,
            titre="Titre unique",
            tags=["IA"],
            niche_detectee="niche inconnue",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_candidats_retourne_none(self, fake_db):
        """
        Exception sur la requete tags (bloc try/except des candidats) ->
        return None sans crasher.
        """
        fake_db.queue_result(FakeResult([]))  # niche vide OK

        def raise_on_tags(s):
            raise RuntimeError("erreur tags &&")
        fake_db.queue_result(raise_on_tags)

        result = await chercher_doublon(
            fake_db,
            titre="Titre",
            tags=["IA"],
            niche_detectee=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_sans_tags_ni_niche_skip_criteres_sql(self, fake_db):
        """
        tags=[] + niche_detectee=None -> seul le critere 3 (LIKE titre)
        peut matcher.
        """
        cand_id = new_uuid()
        # Pas de requete niche (niche_detectee None)
        # Pas de requete tags && (tags vide)
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Mon titre tres proche", []),
        ]))  # LIKE prefixe

        result = await chercher_doublon(
            fake_db,
            titre="Mon titre tres proche",
            tags=[],
            niche_detectee=None,
        )
        # Similarite 1.0 -> match critere 3
        assert result == cand_id

    @pytest.mark.asyncio
    async def test_candidat_deduplique_si_present_dans_2_requetes(self, fake_db):
        """
        Un meme candidat peut apparaitre dans la requete tags ET la requete
        LIKE titre (quand on elargit). La boucle critere 2 utilise un set
        'vus' pour eviter de le traiter 2 fois.
        """
        cand_id = new_uuid()
        # Critere 1 skip
        fake_db.queue_result(FakeResult([]))
        # Critere 2 : 1 candidat avec 2 tags communs (doublon)
        fake_db.queue_result(FakeResult([
            make_row(cand_id, "Titre X", ["IA", "SaaS"]),
        ]))
        # Pas de critere 3 script : le critere 2 trouve avant

        result = await chercher_doublon(
            fake_db,
            titre="Titre X",
            tags=["IA", "SaaS"],
            niche_detectee=None,
        )
        assert result == cand_id
