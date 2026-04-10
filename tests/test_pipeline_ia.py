"""
Tests du service app.services.pipeline_ia.

Couvre :
- _hash_contenu()          : determinisme + insensibilite ordre/casse
- _extraire_json()         : parsing robuste (direct, regex, fences markdown)
- _normaliser_resultat()   : clamping scores, defaults, verdict strict
- _resultat_par_defaut()   : structure valide
- analyser()               : flux complet (cle API absente, cache hit/miss,
                             erreur Claude, JSON illisible, cache absent,
                             propagation source)
"""

import json

import httpx
import pytest

from app.services.pipeline_ia import (
    VERDICTS_VALIDES,
    _extraire_json,
    _hash_contenu,
    _normaliser_resultat,
    _resultat_par_defaut,
    analyser,
)
from tests.conftest import (
    FakeResult,
    assert_resultat_pipeline_valide,
    make_pipeline_result,
)


# ---------------------------------------------------------------------------
# _hash_contenu — fonction pure
# ---------------------------------------------------------------------------

class TestHashContenu:
    """Tests purs du hash de cache."""

    def test_hash_deterministe(self):
        """Meme entree -> meme hash (64 chars hex)."""
        h1 = _hash_contenu("titre", "contenu", ["IA"])
        h2 = _hash_contenu("titre", "contenu", ["IA"])
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_hash_stable_ordre_thematiques(self):
        """L'ordre d'entree des thematiques ne doit pas influer sur le hash."""
        h1 = _hash_contenu("t", "c", ["IA", "SaaS", "Marketing"])
        h2 = _hash_contenu("t", "c", ["Marketing", "SaaS", "IA"])
        h3 = _hash_contenu("t", "c", ["SaaS", "IA", "Marketing"])
        assert h1 == h2 == h3

    def test_hash_insensible_casse(self):
        """Casse du titre/contenu/themes n'impacte pas le hash."""
        h1 = _hash_contenu("Titre", "Contenu", ["IA"])
        h2 = _hash_contenu("TITRE", "CONTENU", ["ia"])
        h3 = _hash_contenu("titre", "contenu", ["Ia"])
        assert h1 == h2 == h3

    def test_hash_insensible_whitespace_bordure(self):
        """Les espaces en debut/fin sont strippes."""
        h1 = _hash_contenu("titre", "contenu", ["IA"])
        h2 = _hash_contenu("  titre  ", "\tcontenu\n", ["IA"])
        assert h1 == h2

    def test_hash_differe_si_contenu_different(self):
        """Un contenu different -> hash different."""
        h1 = _hash_contenu("titre", "contenu A", ["IA"])
        h2 = _hash_contenu("titre", "contenu B", ["IA"])
        assert h1 != h2

    def test_hash_differe_si_thematique_ajoutee(self):
        """Ajouter une thematique change le hash."""
        h1 = _hash_contenu("t", "c", ["IA"])
        h2 = _hash_contenu("t", "c", ["IA", "SaaS"])
        assert h1 != h2


# ---------------------------------------------------------------------------
# _extraire_json — parsing robuste
# ---------------------------------------------------------------------------

class TestExtraireJson:
    """Tests purs du parsing JSON avec fallbacks."""

    def test_json_direct(self):
        """Parse direct d'un JSON propre."""
        assert _extraire_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_json_avec_whitespace(self):
        """Le strip fonctionne en tentative 1."""
        assert _extraire_json("  \n{\"a\": 1}\n  ") == {"a": 1}

    def test_json_avec_preambule(self):
        """Claude ajoute un preambule -> extraction regex {...}."""
        texte = 'Voici le JSON:\n{"a": 1, "b": 2}\nFin.'
        assert _extraire_json(texte) == {"a": 1, "b": 2}

    def test_json_fences_markdown(self):
        """Claude encadre avec ```json ... ``` -> nettoyage."""
        texte = '```json\n{"a": 1}\n```'
        result = _extraire_json(texte)
        assert result == {"a": 1}

    def test_json_fences_sans_langue(self):
        """Fences ``` sans 'json' -> nettoyage aussi."""
        texte = '```\n{"a": 1}\n```'
        result = _extraire_json(texte)
        assert result == {"a": 1}

    def test_json_non_parseable(self):
        """Texte non-JSON -> None."""
        assert _extraire_json("pas du json du tout") is None

    def test_json_vide(self):
        """Texte vide -> None."""
        assert _extraire_json("") is None

    def test_json_imbrique_extrait_premier_objet(self):
        """JSON complexe avec objets imbriques."""
        texte = '{"outer": {"inner": 1}, "list": [1, 2, 3]}'
        result = _extraire_json(texte)
        assert result == {"outer": {"inner": 1}, "list": [1, 2, 3]}


# ---------------------------------------------------------------------------
# _normaliser_resultat — clamping, defaults
# ---------------------------------------------------------------------------

class TestNormaliserResultat:
    """Tests purs de la normalisation du resultat Claude."""

    def test_resultat_nominal_conforme(self):
        """Un dict bien forme reste conforme."""
        brut = make_pipeline_result()
        result = _normaliser_resultat(brut, "titre")
        assert_resultat_pipeline_valide(result)
        assert result["verdict"] == "GO"
        assert result["score_global"] == 7

    def test_score_hors_bornes_clampe(self):
        """Scores > 10 ou < 0 clampes."""
        brut = make_pipeline_result(score_global=99)
        brut["scores"]["demande"]["valeur"] = 15
        brut["scores"]["douleur"]["valeur"] = -3
        result = _normaliser_resultat(brut, "t")
        assert result["score_global"] == 10
        assert result["scores"]["demande"]["valeur"] == 10
        assert result["scores"]["douleur"]["valeur"] == 0

    def test_score_non_numerique_devient_5(self):
        """Valeur non convertible en int -> default 5."""
        brut = make_pipeline_result()
        brut["scores"]["demande"]["valeur"] = "huit"
        brut["score_global"] = "N/A"
        result = _normaliser_resultat(brut, "t")
        assert result["scores"]["demande"]["valeur"] == 5
        assert result["score_global"] == 5

    def test_verdict_invalide_devient_defaut(self):
        """Verdict pas dans {GO,WATCH,SKIP} -> defaut WATCH."""
        brut = make_pipeline_result()
        brut["verdict"] = "MAYBE"
        result = _normaliser_resultat(brut, "t")
        assert result["verdict"] == "WATCH"

    def test_verdict_lowercase_mis_en_majuscule(self):
        """'go' -> 'GO'."""
        brut = make_pipeline_result()
        brut["verdict"] = "go"
        result = _normaliser_resultat(brut, "t")
        assert result["verdict"] == "GO"

    def test_tags_limites_a_3(self):
        """Plus de 3 tags -> tronque a 3."""
        brut = make_pipeline_result()
        brut["tags"] = ["a", "b", "c", "d", "e"]
        result = _normaliser_resultat(brut, "t")
        assert len(result["tags"]) == 3
        assert result["tags"] == ["a", "b", "c"]

    def test_tags_non_liste_devient_vide(self):
        """Si tags n'est pas une liste -> liste vide."""
        brut = make_pipeline_result()
        brut["tags"] = "pas une liste"
        result = _normaliser_resultat(brut, "t")
        assert result["tags"] == []

    def test_mots_cles_seo_limites_a_5(self):
        """Plus de 5 mots-cles SEO -> tronque a 5."""
        brut = make_pipeline_result()
        brut["mots_cles_seo"] = [f"mot{i}" for i in range(10)]
        result = _normaliser_resultat(brut, "t")
        assert len(result["mots_cles_seo"]) == 5

    def test_champs_manquants_remplis_par_defaut(self):
        """Dict vide -> tous les champs presents avec valeurs par defaut."""
        result = _normaliser_resultat({}, "mon titre")
        assert_resultat_pipeline_valide(result)
        # resume_fr devient le titre si absent
        assert result["resume_fr"] == "mon titre"
        # tags et mots_cles_seo vides
        assert result["tags"] == []
        assert result["mots_cles_seo"] == []
        # Verdict par defaut = WATCH (depuis _resultat_par_defaut)
        assert result["verdict"] == "WATCH"
        # Scores par defaut = 5
        assert result["score_global"] == 5
        for dim in ("demande", "douleur", "concurrence", "monetisation"):
            assert result["scores"][dim]["valeur"] == 5

    def test_niche_detectee_none_preserve(self):
        """niche_detectee=None reste None."""
        brut = make_pipeline_result(niche_detectee=None)
        result = _normaliser_resultat(brut, "t")
        assert result["niche_detectee"] is None

    def test_risque_ymyl_cast_bool(self):
        """risque_ymyl cast en bool."""
        brut = make_pipeline_result()
        brut["risque_ymyl"] = 1
        result = _normaliser_resultat(brut, "t")
        assert result["risque_ymyl"] is True

    def test_justification_tronquee_a_300(self):
        """Justification > 300 chars tronquee."""
        brut = make_pipeline_result()
        brut["scores"]["demande"]["justification"] = "x" * 500
        result = _normaliser_resultat(brut, "t")
        assert len(result["scores"]["demande"]["justification"]) == 300


# ---------------------------------------------------------------------------
# _resultat_par_defaut
# ---------------------------------------------------------------------------

class TestResultatParDefaut:
    """Tests du fallback neutre."""

    def test_structure_valide(self):
        result = _resultat_par_defaut("mon titre")
        assert_resultat_pipeline_valide(result)

    def test_resume_tronque_a_280(self):
        """Titre > 280 chars -> resume_fr tronque."""
        result = _resultat_par_defaut("t" * 500)
        assert len(result["resume_fr"]) == 280

    def test_verdict_watch_par_defaut(self):
        """Le verdict neutre est WATCH (pas GO ni SKIP)."""
        assert _resultat_par_defaut("t")["verdict"] == "WATCH"

    def test_scores_tous_a_5(self):
        """Les 4 dimensions + global sont a 5 (neutre)."""
        result = _resultat_par_defaut("t")
        assert result["score_global"] == 5
        for dim in ("demande", "douleur", "concurrence", "monetisation"):
            assert result["scores"][dim]["valeur"] == 5

    def test_raison_dans_verdict_raison(self):
        """La raison fournie apparait dans verdict_raison."""
        result = _resultat_par_defaut("t", raison="cle_api_absente")
        assert "cle_api_absente" in result["verdict_raison"]


# ---------------------------------------------------------------------------
# analyser() — flux complet
# ---------------------------------------------------------------------------

class TestAnalyser:
    """Tests du flux principal avec mocks Claude et session cache."""

    @pytest.mark.asyncio
    async def test_sans_cle_api_retourne_defaut(self, no_anthropic_key):
        """Pas de cle API -> resultat par defaut, pas d'appel Claude."""
        result = await analyser("titre", "contenu", None, ["IA"])
        assert_resultat_pipeline_valide(result)
        assert result["verdict"] == "WATCH"
        assert "cle_api_absente" in result["verdict_raison"]

    @pytest.mark.asyncio
    async def test_nominal_sans_session(
        self, anthropic_key, mock_anthropic_pipeline,
    ):
        """Flux nominal sans session cache : Claude OK -> resultat normalise."""
        reponse = json.dumps(make_pipeline_result())
        mock_anthropic_pipeline(reponse)

        result = await analyser(
            "mon titre", "mon contenu", None, ["IA", "SaaS"],
        )

        assert_resultat_pipeline_valide(result)
        assert result["verdict"] == "GO"
        assert result["score_global"] == 7

    @pytest.mark.asyncio
    async def test_erreur_reseau_fallback_defaut(
        self, anthropic_key, mock_anthropic_raise_pipeline,
    ):
        """Exception Claude (reseau) -> fallback resultat par defaut."""
        mock_anthropic_raise_pipeline(httpx.NetworkError("boom"))

        result = await analyser("titre", "contenu", None, ["IA"])

        assert_resultat_pipeline_valide(result)
        assert "erreur_api" in result["verdict_raison"]

    @pytest.mark.asyncio
    async def test_json_illisible_fallback(
        self, anthropic_key, mock_anthropic_pipeline,
    ):
        """Claude retourne du texte non-JSON -> fallback resultat par defaut."""
        mock_anthropic_pipeline("desole je ne peux pas")

        result = await analyser("titre", "contenu", None, ["IA"])

        assert_resultat_pipeline_valide(result)
        assert "json_illisible" in result["verdict_raison"]

    @pytest.mark.asyncio
    async def test_reponse_vide_fallback(
        self, anthropic_key, mock_anthropic_pipeline,
    ):
        """Claude retourne un message sans content -> fallback."""
        mock_anthropic_pipeline(None)

        result = await analyser("titre", "contenu", None, ["IA"])

        assert_resultat_pipeline_valide(result)
        assert "json_illisible" in result["verdict_raison"]

    @pytest.mark.asyncio
    async def test_cache_hit_pas_d_appel_claude(
        self, anthropic_key, mock_anthropic_pipeline, fake_db,
    ):
        """
        Si le cache retourne un resultat valide, Claude n'est PAS appele.
        Le resultat du cache est retourne tel quel.
        """
        client = mock_anthropic_pipeline('{"verdict": "GO"}')

        # On scripte le cache pour retourner un dict deja present
        resultat_en_cache = make_pipeline_result(verdict="SKIP", score_global=2)
        # _lire_cache fait result.first() puis row[0] -> tuple (dict,)
        fake_db.queue_result(FakeResult([(resultat_en_cache,)]))

        result = await analyser(
            "titre", "contenu", None, ["IA"], session=fake_db,
        )

        # Claude n'a pas ete appele
        client.messages.create.assert_not_awaited()
        # Le resultat vient du cache
        assert result["verdict"] == "SKIP"
        assert result["score_global"] == 2

    @pytest.mark.asyncio
    async def test_cache_miss_ecrit_le_resultat(
        self, anthropic_key, mock_anthropic_pipeline, fake_db,
    ):
        """
        Cache miss -> appel Claude -> ecriture du resultat en cache.
        On verifie que Claude est bien appele ET que INSERT est execute.
        """
        reponse = json.dumps(make_pipeline_result(verdict="WATCH"))
        client = mock_anthropic_pipeline(reponse)

        # Script : cache miss (first() -> None), puis insert (reponse vide)
        fake_db.queue_result(FakeResult([]))       # SELECT cache -> miss
        fake_db.queue_result(FakeResult([]))       # INSERT cache -> ok

        result = await analyser(
            "titre", "contenu", None, ["IA"], session=fake_db,
        )

        # Claude a bien ete appele
        client.messages.create.assert_awaited_once()
        # Le resultat est normalise
        assert result["verdict"] == "WATCH"
        # Un commit a ete fait (par _ecrire_cache)
        assert fake_db.commits >= 1

    @pytest.mark.asyncio
    async def test_cache_table_absente_continue_gracieusement(
        self, anthropic_key, mock_anthropic_pipeline, fake_db_cache_absent,
    ):
        """
        Si la table cache_ia n'existe pas, l'erreur est ignoree et l'appel
        Claude est fait normalement. Le resultat final est retourne malgre
        l'impossibilite d'ecrire en cache.
        """
        reponse = json.dumps(make_pipeline_result(verdict="GO"))
        client = mock_anthropic_pipeline(reponse)

        result = await analyser(
            "titre", "contenu", None, ["IA"],
            session=fake_db_cache_absent,
        )

        # Claude a ete appele malgre l'echec cache
        client.messages.create.assert_awaited_once()
        # Le resultat est bien retourne
        assert_resultat_pipeline_valide(result)
        assert result["verdict"] == "GO"

    @pytest.mark.asyncio
    async def test_source_propagee_au_cache(
        self, anthropic_key, mock_anthropic_pipeline, fake_db,
    ):
        """
        Le parametre source est passe a _ecrire_cache qui le stocke dans
        la colonne source du cache_ia. On verifie via les kwargs de execute().
        """
        reponse = json.dumps(make_pipeline_result())
        mock_anthropic_pipeline(reponse)

        # Intercepter tous les execute() de fake_db pour capturer les params
        execute_calls = []
        original_execute = fake_db.execute

        async def execute_spy(stmt, params=None):
            execute_calls.append({"stmt": str(stmt), "params": params})
            # Script classique : 2 FakeResult vides (cache miss + insert)
            return await original_execute(stmt)

        fake_db.execute = execute_spy
        fake_db.queue_result(FakeResult([]))  # SELECT miss
        fake_db.queue_result(FakeResult([]))  # INSERT

        await analyser(
            "titre", "contenu", None, ["IA"],
            session=fake_db, source="reddit",
        )

        # L'un des execute() doit avoir les params avec source="reddit"
        sources_params = [
            c["params"].get("s") for c in execute_calls
            if c["params"] and "s" in c["params"]
        ]
        assert "reddit" in sources_params

    @pytest.mark.asyncio
    async def test_prompt_contient_titre_et_thematiques(
        self, anthropic_key, mock_anthropic_pipeline,
    ):
        """
        Le prompt envoye a Claude contient le titre, le contenu et les
        thematiques fournies. Vu que _construire_prompt est prive, on
        verifie via les kwargs de messages.create.
        """
        reponse = json.dumps(make_pipeline_result())
        client = mock_anthropic_pipeline(reponse)

        await analyser(
            "mon titre unique",
            "mon contenu specifique",
            {"cle": "valeur"},
            ["IA", "Marketing"],
        )

        call_args = client.messages.create.await_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "mon titre unique" in prompt
        assert "mon contenu specifique" in prompt
        assert "IA" in prompt
        assert "Marketing" in prompt

    @pytest.mark.asyncio
    async def test_verdict_toujours_dans_verdicts_valides(
        self, anthropic_key, mock_anthropic_pipeline,
    ):
        """
        Quel que soit le verdict retourne par Claude (GO/WATCH/SKIP/autre),
        le resultat final est toujours dans VERDICTS_VALIDES.
        """
        for verdict_claude in ("GO", "WATCH", "SKIP", "MAYBE", "yolo", ""):
            reponse = json.dumps(make_pipeline_result(verdict=verdict_claude))
            mock_anthropic_pipeline(reponse)

            result = await analyser("t", "c", None, ["IA"])

            assert result["verdict"] in VERDICTS_VALIDES
