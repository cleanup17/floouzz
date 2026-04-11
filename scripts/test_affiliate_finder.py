"""
Dry-run du Affiliate Finder — version Claude-powered.

Flux hybride :
1. SerpAPI : 1 requete Google.fr ciblee sur les programmes d'affiliation
2. Detection heuristique locale des plateformes mentionnees dans
   les URLs et snippets (Amazon Associates, Awin, CJ, Rakuten,
   ShareASale, programmes directs...)
3. Short-circuit si ZERO plateforme detectee -> verdict AUCUN sans
   appel Claude (economie de cout sur les niches non affiliables)
4. Sinon : appel Claude Sonnet 4.5 avec un prompt expert affiliation
5. Retour JSON structure : score_affiliation, verdict, plateformes,
   programmes, opportunites, verdict_raison

But : valider le prompt et les seuils de detection avant de coder
affiliate_finder.py. Zero BDD, zero cache. Chaque run = 1 appel
SerpAPI + potentiellement 1 appel Claude.

Usage :
    docker compose exec app python -m scripts.test_affiliate_finder "mot cle"
    docker compose exec app python -m scripts.test_affiliate_finder   (defaut ci-dessous)
"""

import asyncio
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

import anthropic
import httpx

from app.config import settings

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# Phase 1 : 2 requetes Google paralleles pour couvrir les 2 mondes de
# l'affiliation FR :
#   - requete 1 : programmes directs des marques specialisees (landing
#     "devenez affilie", "rejoignez notre programme"...)
#   - requete 2 : grandes plateformes (Amazon Associates, Awin, CJ...)
#     via guides d'affilies et pages catalogue qui les mentionnent
# Les resultats sont fusionnes et dedoublonnes par URL avant envoi a Claude.
REQUETES_TEMPLATES = (
    '"{mot_cle}" programme affiliation',
    '{mot_cle} amazon associates OR awin',
)

# 20 resultats par requete, tronque a TOP_N apres dedup
SERPAPI_NUM_RESULTS = 20
TOP_N = 15

MODELE_CLAUDE = "claude-sonnet-4-5"
MAX_TOKENS = 1500

MOT_CLE_DEFAUT = "coquillage allaitement"

MAX_SNIPPET_CHARS = 200

VERDICTS_VALIDES = {"AUCUN", "FAIBLE", "BON", "EXCELLENT"}


# ---------------------------------------------------------------------------
# Detection heuristique des plateformes d'affiliation
# ---------------------------------------------------------------------------

# Dictionnaire plateforme -> patterns (regex case-insensitive) qui permettent
# de l'identifier dans une URL OU dans un snippet. On reste genereux sur les
# patterns pour capturer les variantes linguistiques et les mentions indirectes.
PLATEFORMES_PATTERNS = {
    "Amazon Associates": [
        r"amazon[- ]?associates?",
        r"partenaires?\.amazon",
        r"affiliate-program\.amazon",
        r"amzn\.to",
    ],
    "Awin": [
        r"\bawin\b",
        r"awin1\.com",
    ],
    "CJ Affiliate": [
        r"\bcj\.com\b",
        r"commission junction",
        r"cj affiliate",
    ],
    "Rakuten Advertising": [
        r"rakuten (advertising|marketing|linkshare)",
        r"linkshare",
    ],
    "ShareASale": [
        r"shareasale",
    ],
    "Impact": [
        r"\bimpact\.com\b",
        r"impact radius",
    ],
    "Effiliation": [
        r"effiliation",
    ],
    "TradeDoubler": [
        r"tradedoubler",
    ],
    "Kwanko": [
        r"kwanko",
    ],
    "Programme direct": [
        r"notre programme d[e' ]affiliation",
        r"devenir affilie",
        r"rejoignez notre programme",
        r"affiliate[- ]program(?!.*amazon)",
        r"programme partenaires?",
    ],
}


def _detecter_plateformes(organic_results: list[dict]) -> dict[str, list[int]]:
    """
    Scanne les URLs et snippets pour detecter les plateformes connues.

    Retourne un dict {plateforme_nom: [positions ou elle apparait]} pour
    permettre la tracabilite. Une plateforme peut apparaitre sur plusieurs
    positions, on les liste toutes.

    Regle speciale Amazon : si amazon.fr/amazon.com apparait >= 2 fois
    dans les URLs du top, on considere "Amazon Associates" detecte
    automatiquement. Sinon le produit Amazon n'a aucun pattern d'affiliation
    explicite dans son URL (ex: amazon.fr/Lave-Vitre/dp/B0XXX) et serait
    rate par les patterns REGEX classiques. La presence massive d'Amazon
    dans le top 10 signale que c'est la plateforme d'affiliation par defaut
    de la niche.
    """
    detectees: dict[str, list[int]] = {}

    for i, raw in enumerate(organic_results, 1):
        url = (raw.get("link") or "").lower()
        snippet = (raw.get("snippet") or "").lower()
        title = (raw.get("title") or "").lower()
        texte_complet = f"{url} {title} {snippet}"

        for plateforme, patterns in PLATEFORMES_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, texte_complet, re.IGNORECASE):
                    if plateforme not in detectees:
                        detectees[plateforme] = []
                    if i not in detectees[plateforme]:
                        detectees[plateforme].append(i)
                    break

    # Regle speciale Amazon : detection par presence massive dans le top.
    # On compte les URLs amazon.XX (fr, com, de, be...) qui ne sont PAS deja
    # detectees comme "Amazon Associates" par les patterns classiques.
    positions_amazon: list[int] = []
    for i, raw in enumerate(organic_results, 1):
        url = (raw.get("link") or "").lower()
        if re.search(r"amazon\.(fr|com|be|de|es|it|co\.uk|ca)", url):
            positions_amazon.append(i)

    if len(positions_amazon) >= 2 and "Amazon Associates" not in detectees:
        detectees["Amazon Associates"] = positions_amazon

    return detectees


# ---------------------------------------------------------------------------
# Appel SerpAPI
# ---------------------------------------------------------------------------

async def _fetcher_une_requete(
    client: httpx.AsyncClient, requete: str,
) -> list[dict]:
    """Execute une requete SerpAPI et retourne les organic_results bruts."""
    params = {
        "api_key": settings.SERPAPI_KEY,
        "engine": "google",
        "q": requete,
        "gl": "fr",
        "hl": "fr",
        "num": SERPAPI_NUM_RESULTS,
    }
    response = await client.get(SERPAPI_BASE_URL, params=params)
    response.raise_for_status()
    data = response.json()
    return data.get("organic_results", [])


async def fetcher_resultats(mot_cle: str) -> tuple[list[dict], list[str]]:
    """
    Appelle SerpAPI en parallele avec les 2 requetes de REQUETES_TEMPLATES,
    fusionne les resultats en dedoublonnant par URL, tronque a TOP_N.

    Retourne (organic_results fusionnes tronques, liste des 2 requetes utilisees).
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree dans .env")

    requetes = [tpl.format(mot_cle=mot_cle) for tpl in REQUETES_TEMPLATES]

    async with httpx.AsyncClient(timeout=30) as client:
        resultats_par_requete = await asyncio.gather(
            *(_fetcher_une_requete(client, r) for r in requetes),
            return_exceptions=True,
        )

    # Fusion + dedup par URL (ordre : req 1 priorite, puis req 2 en complement)
    vus: set[str] = set()
    fusionnes: list[dict] = []
    for i, res in enumerate(resultats_par_requete):
        if isinstance(res, Exception):
            print(f"  requete {i+1} erreur : {res}")
            continue
        for item in res:
            url = (item.get("link") or "").strip()
            if not url or url in vus:
                continue
            vus.add(url)
            fusionnes.append(item)

    return fusionnes[:TOP_N], requetes


# ---------------------------------------------------------------------------
# Formatage pour Claude
# ---------------------------------------------------------------------------

def _formatter_resultats_pour_claude(
    organic_results: list[dict],
    plateformes_detectees: dict[str, list[int]],
) -> str:
    """
    Construit un bloc texte pour Claude.
    Pour chaque resultat : position, domaine, title, snippet tronque.
    Ajoute un bloc de synthese des plateformes deja detectees en local.
    """
    lignes = []

    # Synthese des plateformes detectees localement (contexte pour Claude)
    if plateformes_detectees:
        lignes.append("PLATEFORMES DETECTEES PAR HEURISTIQUE LOCALE :")
        for plateforme, positions in plateformes_detectees.items():
            pos_str = ", ".join(f"#{p}" for p in positions)
            lignes.append(f"  - {plateforme} (positions {pos_str})")
        lignes.append("")

    lignes.append("RESULTATS BRUTS :")
    for i, raw in enumerate(organic_results, 1):
        title = (raw.get("title") or "").strip()
        url = (raw.get("link") or "").strip()
        snippet = (raw.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS]

        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""

        lignes.append(f"[{i}] {domaine}")
        lignes.append(f"    Title : {title}")
        lignes.append(f"    URL : {url}")
        if snippet:
            lignes.append(f"    Snippet : {snippet}")
        lignes.append("")

    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Prompt Claude
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """Tu es un expert en monetisation web specialise dans les programmes
d'affiliation, pour accompagner une entrepreneure independante qui cherche
a evaluer si une niche peut etre monetisee via affiliation.

MOT-CLE ANALYSE : {mot_cle}
REQUETES GOOGLE UTILISEES :
{requetes_str}

{resultats_formates}

Ta mission : evaluer si cette niche a un ecosysteme d'affiliation viable
et actionable pour une creatrice de contenu independante.

CRITERES D'EVALUATION :

1. Plateformes detectees : Amazon Associates, Awin, CJ, Rakuten, ShareASale,
   Impact, Effiliation, TradeDoubler, Kwanko, ou programmes directs.
   Plus il y en a, plus la niche est mature cote affiliation.

2. Qualite des programmes :
   - Taux de commission mentionnes (3-5% = bas, 5-10% = moyen, 10%+ = eleve)
   - Duree du cookie (24h = standard Amazon, 30j+ = tres bon)
   - Type de produits (high-ticket = gros revenus par vente,
     low-ticket = volume necessaire)

3. Signaux de niche marginale :
   - Zero programme direct detecte -> dependance totale a Amazon
   - Aucun taux mentionne dans les snippets -> programmes peu communicants
   - Resultats Google tous generiques (pas specifiques a la niche)

4. Signaux de niche mature :
   - 2+ plateformes differentes detectees
   - Taux de commission mentionnes explicitement
   - Programmes directs de marques specialisees
   - Guides/comparatifs d'affilies existants (blogueurs qui gagnent deja)

VERDICT — grille stricte a respecter :
- AUCUN (score 0-1) : 0 plateforme exploitable, niche non affiliable
- FAIBLE (score 2-4) : 1 plateforme unique (souvent Amazon seul), commissions standards
- BON (score 5-7) : 2-3 plateformes, au moins 1 programme avec commission notable
- EXCELLENT (score 8-10) : 3+ plateformes, programmes directs haut de gamme, ecosysteme mature

EXEMPLES DIRECTIONNELS de scoring pour calibrer tes verdicts :
- "cours en ligne meditation" : AUCUN 0/10 (niche service pur, aucune affiliation)
- "prevention burn out" : AUCUN 1/10 (service B2B, affiliation marginale)
- "coquillage allaitement" : FAIBLE 3/10 (Amazon seul, produit de niche peu concurrentiel)
- "lave vitres magnetique" : FAIBLE 4/10 (Amazon + manomano, 1 programme direct occasionnel)
- "matelas bio" : BON 6/10 (Amazon + Awin via Emma/Simba + programmes directs marques)
- "coffret cafe specialty" : BON 7/10 (3+ marques avec programmes directs 8-15% commission)
- "montre connectee sportive" : EXCELLENT 9/10 (Amazon + Garmin direct + Awin + affiliation Fitbit, commissions 5-10%, ecosysteme mature)

N'hesite PAS a utiliser la fourchette complete 0-10. Une niche avec
Amazon Associates seul = FAIBLE 3-4/10 (pas BON). Une niche avec 3+ plateformes
differentes et commissions mentionnees = BON 6-7/10. Une niche avec ecosysteme
multi-plateformes mature = EXCELLENT 8-10/10.

Ta reponse doit etre UN SEUL objet JSON, SANS texte autour, avec cette structure
EXACTE :

{{
  "score_affiliation": <entier 0-10, 0=aucun programme, 10=ecosysteme tres mature>,
  "verdict": "<AUCUN (0-1) / FAIBLE (2-4) / BON (5-7) / EXCELLENT (8-10)>",
  "verdict_raison": "<1-2 phrases pointant les elements concrets du top 10>",
  "plateformes_detectees": [
    "<nom plateforme 1>",
    "<nom plateforme 2>",
    "..."
  ],
  "programmes": [
    {{
      "nom": "<nom du programme observe dans les resultats>",
      "plateforme": "<Amazon Associates / Awin / CJ / Programme direct / etc>",
      "commission": "<taux mentionne ou null si non detecte>",
      "cookie_duree": "<duree mentionnee ou null>",
      "source_url": "<url du snippet d'ou vient l'info, ou null>"
    }}
  ],
  "opportunites": [
    "<piste actionnable 1, max 120 chars>",
    "<piste actionnable 2>",
    "..."
  ]
}}

Liste 1 a 5 programmes concrets maximum. Si aucun programme precis n'est
identifiable malgre la detection de plateformes, retourne une liste vide.
Donne 2-4 opportunites actionnables : angles de contenu qui monetiseraient
bien (comparatif, test, guide d'achat, liste negociee).

Reponds UNIQUEMENT avec l'objet JSON. Pas de markdown, pas de commentaires."""


def _construire_prompt(
    mot_cle: str,
    requetes: list[str],
    resultats_formates: str,
) -> str:
    requetes_str = "\n".join(f"  - {r}" for r in requetes)
    return PROMPT_TEMPLATE.format(
        mot_cle=mot_cle,
        requetes_str=requetes_str,
        resultats_formates=resultats_formates,
    )


# ---------------------------------------------------------------------------
# Parsing JSON robuste
# ---------------------------------------------------------------------------

def _extraire_json(texte: str) -> dict | None:
    """Parse direct -> regex {...} -> nettoyage fences markdown."""
    if not texte:
        return None
    try:
        return json.loads(texte.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", texte, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    nettoye = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.MULTILINE)
    try:
        return json.loads(nettoye)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Normalisation du retour Claude
# ---------------------------------------------------------------------------

def _resultat_par_defaut(
    mot_cle: str,
    raison: str,
    requetes: list[str] | None = None,
) -> dict[str, Any]:
    """Resultat neutre en cas d'echec ou short-circuit."""
    return {
        "mot_cle": mot_cle,
        "score_affiliation": 0,
        "verdict": "AUCUN",
        "verdict_raison": f"Pas d'affiliation detectee ({raison}).",
        "plateformes_detectees": [],
        "programmes": [],
        "opportunites": [],
        "requetes_utilisees": list(requetes or []),
        "nb_resultats_analyses": 0,
    }


def _verdict_depuis_score(score: int) -> str:
    """
    Recalcule le verdict depuis le score pour garantir la coherence de la
    grille (Claude a tendance a ignorer ses propres seuils).
      0-1 -> AUCUN
      2-4 -> FAIBLE
      5-7 -> BON
      8+  -> EXCELLENT
    """
    if score <= 1:
        return "AUCUN"
    if score <= 4:
        return "FAIBLE"
    if score <= 7:
        return "BON"
    return "EXCELLENT"


def _normaliser_resultat_claude(
    brut: dict,
    mot_cle: str,
    requetes: list[str],
    nb_resultats: int,
) -> dict[str, Any]:
    """Valide + clamp le JSON retourne par Claude."""
    def _clamp(v, mini=0, maxi=10) -> int:
        try:
            return max(mini, min(maxi, int(v)))
        except (TypeError, ValueError):
            return 0

    # Le score est la source de verite ; on reconstruit le verdict depuis
    # le score pour eviter les incoherences (ex: Claude retourne AUCUN avec
    # score=3 au lieu de FAIBLE). Le verdict Claude est ignore si present.
    score = _clamp(brut.get("score_affiliation"))
    verdict = _verdict_depuis_score(score)

    plateformes = brut.get("plateformes_detectees", [])
    if not isinstance(plateformes, list):
        plateformes = []
    plateformes = [str(p)[:80] for p in plateformes[:10]]

    programmes_raw = brut.get("programmes", [])
    if not isinstance(programmes_raw, list):
        programmes_raw = []
    programmes = []
    for p in programmes_raw[:5]:
        if not isinstance(p, dict):
            continue
        programmes.append({
            "nom": str(p.get("nom") or "")[:150],
            "plateforme": str(p.get("plateforme") or "")[:80],
            "commission": str(p.get("commission")) if p.get("commission") else None,
            "cookie_duree": str(p.get("cookie_duree")) if p.get("cookie_duree") else None,
            "source_url": str(p.get("source_url")) if p.get("source_url") else None,
        })

    opportunites = brut.get("opportunites", [])
    if not isinstance(opportunites, list):
        opportunites = []
    opportunites = [str(o)[:200] for o in opportunites[:5]]

    return {
        "mot_cle": mot_cle,
        "score_affiliation": score,
        "verdict": verdict,
        "verdict_raison": str(brut.get("verdict_raison") or "")[:500],
        "plateformes_detectees": plateformes,
        "programmes": programmes,
        "opportunites": opportunites,
        "requetes_utilisees": list(requetes),
        "nb_resultats_analyses": nb_resultats,
    }


# ---------------------------------------------------------------------------
# Appel Claude
# ---------------------------------------------------------------------------

async def analyser_via_claude(
    mot_cle: str,
    requetes: list[str],
    organic_results: list[dict],
    plateformes_detectees: dict[str, list[int]],
) -> dict[str, Any]:
    """Envoie les resultats a Claude et retourne un dict normalise."""
    if not settings.ANTHROPIC_API_KEY:
        return _resultat_par_defaut(mot_cle, "cle_api_absente", requetes)

    resultats_formates = _formatter_resultats_pour_claude(
        organic_results, plateformes_detectees,
    )
    prompt = _construire_prompt(mot_cle, requetes, resultats_formates)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
    except Exception as e:
        return _resultat_par_defaut(mot_cle, f"erreur_api: {e}", requetes)

    brut = _extraire_json(reponse)
    if brut is None:
        return _resultat_par_defaut(mot_cle, "json_illisible", requetes)

    return _normaliser_resultat_claude(brut, mot_cle, requetes, len(organic_results))


# ---------------------------------------------------------------------------
# Rendu texte du rapport
# ---------------------------------------------------------------------------

def rendre_rapport(
    mot_cle: str,
    requetes: list[str],
    organic_results: list[dict],
    plateformes_detectees: dict[str, list[int]],
    analyse: dict[str, Any],
    short_circuit: bool,
) -> None:
    print("=" * 76)
    print("Affiliate Finder — dry-run (Claude-powered, hybride 2 requetes)")
    print("=" * 76)
    print(f"Mot-cle         : '{mot_cle}'")
    print("Requetes Google :")
    for r in requetes:
        print(f"  - {r}")
    print(f"Resultats       : {len(organic_results)} collectes (apres dedup)")
    print()

    # Phase 1 : detection heuristique locale
    print("-" * 76)
    print("PHASE 1 — Detection heuristique des plateformes")
    print("-" * 76)
    if plateformes_detectees:
        for plateforme, positions in plateformes_detectees.items():
            pos_str = ", ".join(f"#{p}" for p in positions)
            print(f"  + {plateforme:<30} (positions {pos_str})")
    else:
        print("  Aucune plateforme detectee par heuristique.")
    print()

    # Phase 2 : verdict Claude ou short-circuit
    print("-" * 76)
    if short_circuit:
        print("PHASE 2 — SHORT-CIRCUIT (aucune plateforme, pas d'appel Claude)")
    else:
        print("PHASE 2 — Analyse Claude")
    print("-" * 76)

    verdict = analyse["verdict"]
    print(f"Score affiliation : {analyse['score_affiliation']}/10 — [{verdict}]")
    print(f"Raison            : {analyse['verdict_raison']}")
    print()

    # Plateformes confirmees
    if analyse.get("plateformes_detectees"):
        print(f"Plateformes ({len(analyse['plateformes_detectees'])}) :")
        for p in analyse["plateformes_detectees"]:
            print(f"  - {p}")
        print()

    # Programmes
    if analyse.get("programmes"):
        print(f"Programmes identifies ({len(analyse['programmes'])}) :")
        for prog in analyse["programmes"]:
            print(f"  > {prog.get('nom', '?')} ({prog.get('plateforme', '?')})")
            if prog.get("commission"):
                print(f"    commission : {prog['commission']}")
            if prog.get("cookie_duree"):
                print(f"    cookie     : {prog['cookie_duree']}")
        print()

    # Opportunites
    if analyse.get("opportunites"):
        print(f"Opportunites ({len(analyse['opportunites'])}) :")
        for o in analyse["opportunites"]:
            print(f"  + {o}")
        print()

    # Top resultats bruts (traceabilite)
    print("=" * 76)
    print("Top resultats bruts (pour debug)")
    print("=" * 76)
    for i, raw in enumerate(organic_results[:10], 1):
        title = (raw.get("title") or "")[:70]
        url = (raw.get("link") or "")[:90]
        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""
        print(f"[{i:>2}] {domaine}")
        print(f"     {title}")
    print()
    print("Dry-run termine. Aucune donnee persistee, aucun cache.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    mot_cle = sys.argv[1] if len(sys.argv) > 1 else MOT_CLE_DEFAUT

    # Phase 1 : 2 requetes SerpAPI paralleles
    try:
        organic_results, requetes = await fetcher_resultats(mot_cle)
    except RuntimeError as e:
        print(f"ERREUR : {e}")
        return
    except Exception as e:
        print(f"ERREUR SerpAPI : {e}")
        return

    if not organic_results:
        print(f"Aucun resultat SerpAPI pour '{mot_cle}'.")
        analyse = _resultat_par_defaut(mot_cle, "aucun_resultat_serp", requetes)
        rendre_rapport(mot_cle, requetes, [], {}, analyse, short_circuit=True)
        return

    # Detection heuristique locale
    plateformes_detectees = _detecter_plateformes(organic_results)

    print(f"SerpAPI : {len(organic_results)} resultats collectes (apres dedup)")
    print(f"Phase 1 : {len(plateformes_detectees)} plateforme(s) detectee(s)")
    print()

    # Short-circuit si zero plateforme
    if not plateformes_detectees:
        analyse = _resultat_par_defaut(
            mot_cle, "aucune_plateforme_detectee", requetes,
        )
        analyse["nb_resultats_analyses"] = len(organic_results)
        rendre_rapport(
            mot_cle, requetes, organic_results, {}, analyse,
            short_circuit=True,
        )
        return

    # Sinon : appel Claude
    print("Plateformes detectees -> appel Claude en cours...")
    print()
    analyse = await analyser_via_claude(
        mot_cle, requetes, organic_results, plateformes_detectees,
    )

    rendre_rapport(
        mot_cle, requetes, organic_results, plateformes_detectees,
        analyse, short_circuit=False,
    )


if __name__ == "__main__":
    asyncio.run(main())
