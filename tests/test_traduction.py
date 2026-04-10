"""
Tests du service app.services.traduction.

Couvre :
- _besoin_traduction() : heuristique de detection FR
- traduire() : flux unitaire titre+contenu (mock Claude)
- traduire_titres() : flux batch avec ordre preserve, filtrage FR,
  decoupage en lots, fallbacks sur erreur/JSON illisible
"""

import json

import httpx
import pytest

from app.services.traduction import (
    MAX_CONTENU_CHARS,
    MAX_TITRES_PAR_BATCH,
    _besoin_traduction,
    traduire,
    traduire_titres,
)


# ---------------------------------------------------------------------------
# Heuristique _besoin_traduction
# ---------------------------------------------------------------------------

class TestBesoinTraduction:
    """Tests purs — pas de mock, juste la fonction."""

    def test_anglais_pur_a_traduire(self):
        """Un titre clairement anglais doit retourner True."""
        assert _besoin_traduction("How to build a SaaS", "") is True

    def test_francais_evident_pas_a_traduire(self):
        """Un texte avec >=2 marqueurs FR doit retourner False."""
        # "pour", "les" -> 2 marqueurs, heuristique declare FR
        assert _besoin_traduction(
            "Comment construire un SaaS pour les petites entreprises",
            "",
        ) is False

    def test_texte_court_ambigu_a_traduire(self):
        """Dans le doute (texte court), on traduit."""
        assert _besoin_traduction("test", "") is True

    def test_un_seul_marqueur_fr_insuffisant(self):
        """1 marqueur FR seul = en-dessous du seuil, on traduit quand meme."""
        # Un seul "le" ne suffit pas (seuil >= 2)
        assert _besoin_traduction("le test", "") is True

    def test_marqueurs_dans_contenu(self):
        """Les marqueurs peuvent etre dans le contenu, pas que le titre."""
        assert _besoin_traduction(
            "Short title",
            "voici un texte pour tester la detection dans le contenu",
        ) is False


# ---------------------------------------------------------------------------
# traduire() — flux unitaire
# ---------------------------------------------------------------------------

class TestTraduire:
    """Tests de la fonction traduire() — mock Anthropic via fixture."""

    @pytest.mark.asyncio
    async def test_sans_cle_api_retourne_original(self, no_anthropic_key):
        """Sans cle API, retourne le couple inchange sans appel Claude."""
        titre, contenu = await traduire("English title", "English content")
        assert titre == "English title"
        assert contenu == "English content"

    @pytest.mark.asyncio
    async def test_deja_francais_pas_d_appel_claude(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Si le texte est detecte FR, aucun appel Claude."""
        client = mock_anthropic_traduction('{"titre": "X", "contenu": "Y"}')

        titre, contenu = await traduire(
            "Comment construire un SaaS pour les petites entreprises",
            "",
        )

        assert titre == "Comment construire un SaaS pour les petites entreprises"
        assert contenu == ""
        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_traduction_nominale(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude retourne un JSON propre -> couple traduit."""
        mock_anthropic_traduction(
            '{"titre": "Comment construire un SaaS", "contenu": "Guide etape par etape"}'
        )

        titre, contenu = await traduire(
            "How to build a SaaS",
            "Step by step guide",
        )

        assert titre == "Comment construire un SaaS"
        assert contenu == "Guide etape par etape"

    @pytest.mark.asyncio
    async def test_erreur_reseau_fallback(
        self, anthropic_key, mock_anthropic_raise_traduction,
    ):
        """Exception reseau Claude -> fallback sur le couple original."""
        mock_anthropic_raise_traduction(httpx.NetworkError("boom"))

        titre, contenu = await traduire("How to X", "Content Y")

        assert titre == "How to X"
        assert contenu == "Content Y"

    @pytest.mark.asyncio
    async def test_json_illisible_fallback(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude retourne du texte non-JSON -> fallback."""
        mock_anthropic_traduction("désolé je ne peux pas traduire")

        titre, contenu = await traduire("How to X", "Content Y")

        assert titre == "How to X"
        assert contenu == "Content Y"

    @pytest.mark.asyncio
    async def test_json_avec_preambule_extrait(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude ajoute du texte avant/apres le JSON -> extraction regex."""
        mock_anthropic_traduction(
            'Voici la traduction:\n{"titre": "Titre FR", "contenu": "Contenu FR"}\nVoila.'
        )

        titre, contenu = await traduire("Original title", "Original content")

        assert titre == "Titre FR"
        assert contenu == "Contenu FR"

    @pytest.mark.asyncio
    async def test_troncature_contenu_au_prompt(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Le contenu envoye a Claude est tronque a MAX_CONTENU_CHARS."""
        client = mock_anthropic_traduction(
            '{"titre": "T", "contenu": "C"}'
        )

        # Marqueur unique (caractere absent du prompt fixe) pour compter sans
        # interference. 'z' n'apparait pas dans le prompt de traduction.
        contenu_long = "z" * (MAX_CONTENU_CHARS + 500)
        await traduire("English title", contenu_long)

        call_args = client.messages.create.await_args
        prompt_envoye = call_args.kwargs["messages"][0]["content"]

        nb_z = prompt_envoye.count("z")
        assert nb_z == MAX_CONTENU_CHARS, (
            f"Attendu {MAX_CONTENU_CHARS} 'z' (contenu tronque), recu {nb_z}"
        )

    @pytest.mark.asyncio
    async def test_reponse_vide_fallback(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude retourne un message sans content -> fallback."""
        mock_anthropic_traduction(None)

        titre, contenu = await traduire("Some EN title", "Some EN content")

        assert titre == "Some EN title"
        assert contenu == "Some EN content"


# ---------------------------------------------------------------------------
# traduire_titres() — flux batch
# ---------------------------------------------------------------------------

class TestTraduireTitres:
    """Tests de la fonction traduire_titres() — batch avec ordre preserve."""

    @pytest.mark.asyncio
    async def test_liste_vide(self, anthropic_key, mock_anthropic_traduction):
        """Liste vide -> liste vide, pas d'appel Claude."""
        client = mock_anthropic_traduction("[]")
        assert await traduire_titres([]) == []
        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sans_cle_api_retourne_original(self, no_anthropic_key):
        """Pas de cle API -> liste inchangee."""
        titres = ["How to X", "Why Y", "What about Z"]
        assert await traduire_titres(titres) == titres

    @pytest.mark.asyncio
    async def test_tous_francais_pas_d_appel(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Tous les titres detectes FR -> aucun appel Claude."""
        client = mock_anthropic_traduction("[]")
        titres = [
            "Comment construire un SaaS pour les petites entreprises",
            "Pourquoi les startups ont besoin de la France dans le monde",
        ]
        result = await traduire_titres(titres)
        assert result == titres
        client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_traduction_nominale_ordre_preserve(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """5 titres EN -> 5 traductions FR dans l'ordre."""
        reponse = json.dumps([
            {"index": 0, "titre": "Titre 0"},
            {"index": 1, "titre": "Titre 1"},
            {"index": 2, "titre": "Titre 2"},
            {"index": 3, "titre": "Titre 3"},
            {"index": 4, "titre": "Titre 4"},
        ])
        mock_anthropic_traduction(reponse)

        titres = [f"English title {i}" for i in range(5)]
        result = await traduire_titres(titres)

        assert result == ["Titre 0", "Titre 1", "Titre 2", "Titre 3", "Titre 4"]

    @pytest.mark.asyncio
    async def test_ordre_preserve_via_index_quand_claude_reordonne(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude retourne les items dans le desordre -> reordonnes via index."""
        reponse = json.dumps([
            {"index": 2, "titre": "C"},
            {"index": 0, "titre": "A"},
            {"index": 1, "titre": "B"},
        ])
        mock_anthropic_traduction(reponse)

        result = await traduire_titres(["alpha", "beta", "gamma"])

        assert result == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_decoupage_en_lots(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """25 titres -> 2 batches (20 + 5), 2 appels Claude."""
        # Meme reponse pour les 2 batches, les index sont relatifs au lot
        reponse = json.dumps([
            {"index": i, "titre": f"FR-{i}"} for i in range(20)
        ])
        client = mock_anthropic_traduction(reponse)

        titres = [f"English {i}" for i in range(25)]
        await traduire_titres(titres)

        assert client.messages.create.await_count == 2

    @pytest.mark.asyncio
    async def test_mix_fr_en_seuls_en_envoyes(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """
        Liste mixte FR/EN -> seuls les EN sont envoyes a Claude,
        les FR gardes tels quels dans le resultat final.
        """
        # Index 0 et 2 sont EN (seront envoyes), index 1 est FR
        # Le lot envoye contient 2 titres dans l'ordre [0, 2] du lot
        reponse = json.dumps([
            {"index": 0, "titre": "Comment X"},
            {"index": 1, "titre": "Comment Z"},
        ])
        client = mock_anthropic_traduction(reponse)

        titres = [
            "How to X",
            "Pourquoi les startups ont besoin de la France ici",
            "How to Z",
        ]
        result = await traduire_titres(titres)

        # Position 0 traduite, position 1 inchangee, position 2 traduite
        assert result[0] == "Comment X"
        assert result[1] == "Pourquoi les startups ont besoin de la France ici"
        assert result[2] == "Comment Z"
        # Un seul appel Claude (les 2 titres EN tiennent dans un lot)
        assert client.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_erreur_reseau_retourne_original(
        self, anthropic_key, mock_anthropic_raise_traduction,
    ):
        """Exception reseau sur _traduire_lot -> liste originale."""
        mock_anthropic_raise_traduction(httpx.NetworkError("boom"))

        titres = ["How to X", "Why Y"]
        result = await traduire_titres(titres)

        assert result == titres

    @pytest.mark.asyncio
    async def test_json_illisible_retourne_original(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Claude retourne du non-JSON -> liste originale preservee."""
        mock_anthropic_traduction("désolé pas possible")

        titres = ["How to X", "Why Y"]
        result = await traduire_titres(titres)

        assert result == titres

    @pytest.mark.asyncio
    async def test_item_sans_index_ignore(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """Un item malforme dans la reponse Claude est ignore, pas de crash."""
        reponse = json.dumps([
            {"index": 0, "titre": "FR zero"},
            {"pas_index": "oops"},  # malforme
            {"index": 2, "titre": "FR deux"},
        ])
        mock_anthropic_traduction(reponse)

        titres = ["How A", "How B", "How C"]
        result = await traduire_titres(titres)

        # Index 0 et 2 traduits, index 1 reste original (item malforme ignore)
        assert result[0] == "FR zero"
        assert result[1] == "How B"
        assert result[2] == "FR deux"

    @pytest.mark.asyncio
    async def test_max_titres_par_batch_est_respecte(
        self, anthropic_key, mock_anthropic_traduction,
    ):
        """
        Le decoupage respecte MAX_TITRES_PAR_BATCH : exactement
        ceil(N / MAX) appels pour N titres.
        """
        reponse = json.dumps([{"index": 0, "titre": "X"}])
        client = mock_anthropic_traduction(reponse)

        # MAX + 1 titres -> 2 appels
        n = MAX_TITRES_PAR_BATCH + 1
        titres = [f"English {i}" for i in range(n)]
        await traduire_titres(titres)

        assert client.messages.create.await_count == 2
