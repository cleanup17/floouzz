"""
Service Affiliate Finder — detection de programmes d'affiliation via Claude.

Pour un mot-cle donne :
1. Lance 2 requetes Google.fr paralleles via SerpAPI pour couvrir les 2
   mondes de l'affiliation FR : programmes directs des marques specialisees
   et grandes plateformes (Amazon Associates, Awin, CJ...)
2. Fusionne et dedoublonne les resultats par URL
3. Detection heuristique locale des plateformes connues (regle speciale
   Amazon : presence massive >= 2 URLs amazon.XX)
4. Short-circuit si ZERO plateforme detectee -> verdict AUCUN sans
   appel Claude (economie sur les niches non affiliables)
5. Sinon : envoie les resultats a Claude Sonnet 4.5 avec un prompt expert
   affiliation et exemples directionnels
6. Post-traitement strict : verdict recalcule depuis le score pour
   garantir la coherence de la grille
7. Cache 30 jours dans cache_ia (source='affiliate_finder')

Fallbacks : pas de cle API, erreur SerpAPI, erreur Claude, JSON illisible ->
resultat neutre par defaut pour ne jamais crasher le mode Analyse.
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import anthropic
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SERPAPI_BASE_URL = "https://serpapi.com/search.json"

# 2 requetes paralleles pour couvrir programmes directs + grandes plateformes
REQUETES_TEMPLATES = (
    '"{mot_cle}" programme affiliation',
    '{mot_cle} amazon associates OR awin',
)

# 20 resultats par requete, tronque a TOP_N apres dedup
SERPAPI_NUM_RESULTS = 20
TOP_N = 15

MODELE_CLAUDE = "claude-sonnet-4-5"
MAX_TOKENS = 1500

# Cache 30 jours : les programmes d'affiliation changent tres peu, les taux
# de commission evoluent lentement, les fermetures sont rares. Le TTL le plus
# long des 3 services Floouzz (pipeline_ia=24h, serp_gap=7j, affiliate=30j).
CACHE_TTL_HEURES = 24 * 30

# Troncature du snippet envoye a Claude (economie de tokens)
MAX_SNIPPET_CHARS = 200

VERDICTS_VALIDES = {"AUCUN", "FAIBLE", "BON", "EXCELLENT"}

CACHE_SOURCE = "affiliate_finder"


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

# Regex pour la regle speciale Amazon : presence massive dans le top
AMAZON_DOMAIN_RE = re.compile(r"amazon\.(fr|com|be|de|es|it|co\.uk|ca)")


def _detecter_plateformes(organic_results: list[dict]) -> dict[str, list[int]]:
    """
    Scanne les URLs et snippets pour detecter les plateformes connues.

    Retourne un dict {plateforme_nom: [positions]} pour tracabilite.

    Regle speciale Amazon : si amazon.XX apparait >= 2 fois dans les URLs
    du top, on considere "Amazon Associates" detecte automatiquement. Sinon
    un produit Amazon (ex: amazon.fr/dp/B0XXX) serait rate par les patterns
    REGEX classiques. La presence massive d'Amazon dans le top 10 signale
    que c'est la plateforme d'affiliation par defaut de la niche.
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

    # Regle speciale Amazon : detection par presence massive
    positions_amazon: list[int] = []
    for i, raw in enumerate(organic_results, 1):
        url = (raw.get("link") or "").lower()
        if AMAZON_DOMAIN_RE.search(url):
            positions_amazon.append(i)

    if len(positions_amazon) >= 2 and "Amazon Associates" not in detectees:
        detectees["Amazon Associates"] = positions_amazon

    return detectees


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback)
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


# ---------------------------------------------------------------------------
# Cache PostgreSQL (reutilise la table cache_ia)
# ---------------------------------------------------------------------------

def _hash_mot_cle(mot_cle: str) -> str:
    """
    Hash stable du mot-cle normalise.
    Prefixe 'affiliate:' pour eviter toute collision avec pipeline_ia
    ou serp_gap.
    """
    payload = f"affiliate:{mot_cle.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lire_cache(session: AsyncSession, hash_contenu: str) -> dict[str, Any] | None:
    """Recupere un resultat en cache si valide (non expire)."""
    try:
        result = await session.execute(
            text(
                "SELECT resultat FROM cache_ia "
                "WHERE hash_contenu = :h AND expires_at > NOW() "
                "LIMIT 1"
            ),
            {"h": hash_contenu},
        )
        row = result.first()
        if row is None:
            return None
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.debug(f"Affiliate Finder : cache indisponible (lecture) — {e}")
        return None


async def _ecrire_cache(
    session: AsyncSession,
    hash_contenu: str,
    resultat: dict[str, Any],
) -> None:
    """Ecrit un resultat en cache avec TTL 30 jours."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HEURES)
    try:
        await session.execute(
            text(
                "INSERT INTO cache_ia (hash_contenu, source, resultat, expires_at) "
                "VALUES (:h, :s, CAST(:r AS JSONB), :e) "
                "ON CONFLICT (hash_contenu) DO UPDATE "
                "SET source = EXCLUDED.source, "
                "    resultat = EXCLUDED.resultat, "
                "    expires_at = EXCLUDED.expires_at"
            ),
            {
                "h": hash_contenu,
                "s": CACHE_SOURCE,
                "r": json.dumps(resultat, ensure_ascii=False),
                "e": expires_at,
            },
        )
        await session.commit()
    except Exception as e:
        logger.debug(f"Affiliate Finder : cache indisponible (ecriture) — {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Appel SerpAPI (2 requetes paralleles)
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


async def _fetcher_resultats(mot_cle: str) -> tuple[list[dict], list[str]]:
    """
    Appelle SerpAPI en parallele avec les 2 requetes, fusionne les resultats
    en dedoublonnant par URL, tronque a TOP_N.

    Retourne (organic_results fusionnes tronques, liste des 2 requetes).
    Leve RuntimeError si SERPAPI_KEY absente.
    """
    if not settings.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY non configuree")

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
            logger.warning(f"Affiliate Finder : requete {i+1} erreur — {res}")
            continue
        for item in res:
            url = (item.get("link") or "").strip()
            if not url or url in vus:
                continue
            vus.add(url)
            fusionnes.append(item)

    return fusionnes[:TOP_N], requetes


# ---------------------------------------------------------------------------
# Formatage des resultats pour Claude
# ---------------------------------------------------------------------------

def _formatter_resultats_pour_claude(
    organic_results: list[dict],
    plateformes_detectees: dict[str, list[int]],
) -> str:
    """
    Construit un bloc texte compact pour Claude.
    Pre-injecte la synthese heuristique locale pour que Claude valide ou
    nuance la detection automatique au lieu de refaire le travail.
    """
    lignes = []

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


def _extraire_top_structure(organic_results: list[dict]) -> list[dict]:
    """
    Construit la liste structuree du top pour stockage (cache + BDD).
    Plus concis que les organic_results bruts de SerpAPI.
    """
    top = []
    for i, raw in enumerate(organic_results, 1):
        url = (raw.get("link") or "").strip()
        try:
            domaine = urlparse(url).netloc.lower()
        except Exception:
            domaine = ""
        top.append({
            "position": i,
            "titre": (raw.get("title") or "").strip(),
            "url": url,
            "domaine": domaine,
            "snippet": (raw.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS],
        })
    return top


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
  "verdict_raison": "<1-2 phrases pointant les elements concrets du top>",
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
# Normalisation du retour Claude + post-traitement verdict/score
# ---------------------------------------------------------------------------

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
    """
    Valide + clamp le JSON retourne par Claude.
    Le verdict est RECONSTRUIT depuis le score (source de verite) pour
    eviter les incoherences (ex: Claude retourne AUCUN avec score=3).
    """
    def _clamp(v, mini=0, maxi=10) -> int:
        try:
            return max(mini, min(maxi, int(v)))
        except (TypeError, ValueError):
            return 0

    # Score = source de verite, verdict = derive
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
# Fonction principale
# ---------------------------------------------------------------------------

async def chercher_affiliation(
    mot_cle: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """
    Analyse l'ecosysteme d'affiliation d'un mot-cle : top 10 Google ciblé
    sur l'affiliation + detection heuristique + jugement expert Claude.

    Args:
        mot_cle: mot-cle a analyser (normalise pour le cache)
        session: session SQLAlchemy async pour le cache (optionnelle).
            Si fournie, lit/ecrit dans cache_ia avec TTL 30 jours.

    Returns:
        dict avec les cles :
        - mot_cle : str
        - score_affiliation : int (0-10)
        - verdict : str (AUCUN / FAIBLE / BON / EXCELLENT)
        - verdict_raison : str
        - plateformes_detectees : list[str]
        - programmes : list[dict] (nom, plateforme, commission, cookie_duree, source_url)
        - opportunites : list[str]
        - requetes_utilisees : list[str] (2 requetes Google)
        - nb_resultats_analyses : int

    Tous les chemins d'erreur retournent un resultat par defaut neutre
    (score 0, verdict AUCUN) pour ne jamais crasher l'appelant.
    """
    if not mot_cle or not mot_cle.strip():
        return _resultat_par_defaut(mot_cle, "mot_cle_vide")

    # --- Cache hit ? ----------------------------------------------------------
    hash_cle = _hash_mot_cle(mot_cle)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_cle)
        if cache_hit is not None:
            logger.info(f"Affiliate Finder : cache hit pour '{mot_cle[:40]}'")
            return cache_hit

    # --- Phase 1 : appels SerpAPI paralleles ---------------------------------
    try:
        organic_results, requetes = await _fetcher_resultats(mot_cle)
    except RuntimeError as e:
        logger.warning(f"Affiliate Finder : SerpAPI indisponible — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_indisponible")
    except Exception as e:
        logger.error(f"Affiliate Finder : erreur SerpAPI — {e}")
        return _resultat_par_defaut(mot_cle, "serpapi_erreur")

    if not organic_results:
        logger.info(f"Affiliate Finder : aucun resultat SerpAPI pour '{mot_cle[:40]}'")
        return _resultat_par_defaut(mot_cle, "aucun_resultat_serp", requetes)

    # --- Detection heuristique locale -----------------------------------------
    plateformes_detectees = _detecter_plateformes(organic_results)

    # --- Short-circuit : zero plateforme detectee -> AUCUN sans Claude --------
    if not plateformes_detectees:
        logger.info(
            f"Affiliate Finder : aucune plateforme detectee pour "
            f"'{mot_cle[:40]}' — short-circuit"
        )
        resultat = _resultat_par_defaut(
            mot_cle, "aucune_plateforme_detectee", requetes,
        )
        resultat["nb_resultats_analyses"] = len(organic_results)
        # Ecriture cache meme sur short-circuit (evite de re-spam SerpAPI)
        if session is not None:
            await _ecrire_cache(session, hash_cle, resultat)
        return resultat

    # --- Phase 2 : appel Claude -----------------------------------------------
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("Affiliate Finder : ANTHROPIC_API_KEY non configuree")
        resultat = _resultat_par_defaut(mot_cle, "cle_api_claude_absente", requetes)
        resultat["nb_resultats_analyses"] = len(organic_results)
        return resultat

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
        logger.info(
            f"Affiliate Finder : reponse Claude ({len(reponse)} chars) "
            f"pour '{mot_cle[:40]}'"
        )
    except Exception as e:
        logger.error(f"Affiliate Finder : erreur Claude — {e}")
        resultat = _resultat_par_defaut(mot_cle, "erreur_api_claude", requetes)
        resultat["nb_resultats_analyses"] = len(organic_results)
        return resultat

    # --- Parsing + normalisation ----------------------------------------------
    brut = _extraire_json(reponse)
    if brut is None:
        logger.warning(
            f"Affiliate Finder : JSON illisible — fallback ({reponse[:200]})"
        )
        resultat = _resultat_par_defaut(mot_cle, "json_illisible", requetes)
        resultat["nb_resultats_analyses"] = len(organic_results)
        return resultat

    resultat = _normaliser_resultat_claude(
        brut, mot_cle, requetes, len(organic_results),
    )

    # --- Ecriture cache 30 jours ----------------------------------------------
    if session is not None:
        await _ecrire_cache(session, hash_cle, resultat)

    return resultat
