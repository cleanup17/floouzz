"""
Service pipeline_ia — enrichissement + scoring en un seul appel Claude.

Remplace enrichissement.py + scoring.py par un pipeline unifie :
    contenu FR -> Claude API -> JSON structure (resume, tags, scores, verdict)

L'entree est TOUJOURS en francais. La traduction des sources anglophones
est faite en amont par traduction.py, avant l'appel a ce service.

Cache PostgreSQL TTL 24h pour eviter les appels doublons sur un meme contenu.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# Duree de vie du cache pour un meme contenu analyse
CACHE_TTL_HEURES = 24

# Modele Claude utilise pour le pipeline — Sonnet pour un meilleur jugement
# sur verdict/YMYL que Haiku
MODELE_CLAUDE = "claude-sonnet-4-5"

# Verdicts autorises — tout le reste sera normalise vers SKIP
VERDICTS_VALIDES = {"GO", "WATCH", "SKIP"}


# ---------------------------------------------------------------------------
# Resultat par defaut (fallback en cas d'erreur ou cle API manquante)
# ---------------------------------------------------------------------------

def _resultat_par_defaut(titre: str, raison: str = "pipeline_indisponible") -> dict[str, Any]:
    """Retourne un resultat neutre quand Claude n'est pas disponible ou echoue."""
    return {
        "resume_fr": titre[:280],
        "tags": [],
        "niche_detectee": None,
        "scores": {
            "demande": {"valeur": 5, "justification": "Non evalue — pipeline indisponible."},
            "douleur": {"valeur": 5, "justification": "Non evalue — pipeline indisponible."},
            "concurrence": {"valeur": 5, "justification": "Non evalue — pipeline indisponible."},
            "monetisation": {"valeur": 5, "justification": "Non evalue — pipeline indisponible."},
        },
        "score_global": 5,
        "verdict": "WATCH",
        "verdict_raison": f"Analyse IA non disponible ({raison}) — verdict neutre par defaut.",
        "mots_cles_seo": [],
        "risque_ymyl": False,
    }


# ---------------------------------------------------------------------------
# Cache PostgreSQL (table cache_ia — creee a l'etape 2 du plan)
# ---------------------------------------------------------------------------

def _hash_contenu(titre: str, contenu: str, thematiques: list[str]) -> str:
    """
    Calcule un hash stable du contenu a analyser.

    Sert de cle de cache : deux appels avec le meme titre/contenu/thematiques
    toucheront la meme entree en base.
    """
    # On normalise en triant les thematiques pour que l'ordre ne casse pas le cache
    payload = json.dumps(
        {
            "titre": titre.strip().lower(),
            "contenu": contenu.strip().lower(),
            "thematiques": sorted(t.lower() for t in thematiques),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lire_cache(session: AsyncSession, hash_contenu: str) -> dict[str, Any] | None:
    """
    Recupere un resultat en cache si valide (non expire).
    Retourne None si absent, expire, ou si la table n'existe pas encore.
    """
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
        # Le champ resultat est en JSONB — SQLAlchemy le decode deja en dict
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        # Table absente ou erreur DB — on ignore le cache et on continue
        logger.debug(f"Cache IA indisponible (lecture) : {e}")
        return None


async def _ecrire_cache(
    session: AsyncSession,
    hash_contenu: str,
    resultat: dict[str, Any],
    source: str | None,
) -> None:
    """
    Ecrit un resultat en cache avec TTL 24h. UPSERT pour eviter les doublons.
    La source permet une invalidation ciblee (ex: purger tout le cache reddit).
    Echec silencieux si la table n'existe pas encore.
    """
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
                "s": source,
                "r": json.dumps(resultat, ensure_ascii=False),
                "e": expires_at,
            },
        )
        await session.commit()
    except Exception as e:
        logger.debug(f"Cache IA indisponible (ecriture) : {e}")
        await session.rollback()


# ---------------------------------------------------------------------------
# Construction du prompt + parsing robuste
# ---------------------------------------------------------------------------

def _construire_prompt(
    titre: str,
    contenu: str,
    donnees: dict | None,
    thematiques: list[str],
) -> str:
    """Construit le prompt Claude qui force un JSON structure en retour."""
    themes_str = ", ".join(thematiques) if thematiques else (
        "IA, SaaS, E-commerce, Marketing, Video, Finance, Metiers & RH, "
        "Sante, Education, Innovation, Automatisation, Creation"
    )

    # On tronque les donnees brutes pour ne pas exploser le prompt
    donnees_str = ""
    if donnees:
        donnees_str = json.dumps(donnees, ensure_ascii=False, default=str)[:800]

    return f"""Tu es un analyste de marche qui aide une consultante numerique solo a reperer
des niches pour micro-SaaS ou offres de service IA.

THEMATIQUES DE REFERENCE : {themes_str}

SIGNAL A ANALYSER :
Titre : {titre}
Contenu : {contenu[:2000]}
Donnees brutes : {donnees_str}

Ta mission : produire UN SEUL objet JSON, SANS texte autour, avec cette structure exacte :

{{
  "resume_fr": "<1-2 phrases en francais, 280 caracteres max>",
  "tags": ["<1 a 3 thematiques de la liste>"],
  "niche_detectee": "<mot-cle de niche en francais, ou null>",
  "scores": {{
    "demande": {{"valeur": <0-10>, "justification": "<1 phrase>"}},
    "douleur": {{"valeur": <0-10>, "justification": "<1 phrase>"}},
    "concurrence": {{"valeur": <0-10>, "justification": "<1 phrase, 10 = peu de concurrents>"}},
    "monetisation": {{"valeur": <0-10>, "justification": "<1 phrase>"}}
  }},
  "score_global": <0-10, moyenne ponderee : demande 40%, douleur 20%, concurrence 20%, monetisation 20%>,
  "verdict": "<GO si score_global >= 7, WATCH si 4-6, SKIP si < 4>",
  "verdict_raison": "<1 phrase qui justifie le verdict>",
  "mots_cles_seo": ["<3 a 5 mots-cles SEO en francais>"],
  "risque_ymyl": <true si le sujet touche sante, finance, juridique, securite ; false sinon>
}}

Reponds UNIQUEMENT avec cet objet JSON, rien d'autre. Pas de markdown, pas de commentaires."""


def _extraire_json(texte: str) -> dict[str, Any] | None:
    """
    Extrait le premier objet JSON valide d'un texte.

    Gere le cas ou Claude ajoute du texte avant/apres (markdown, preambule...).
    Retourne None si aucun JSON parseable trouve.
    """
    if not texte:
        return None

    # Tentative 1 : parse direct
    try:
        return json.loads(texte.strip())
    except json.JSONDecodeError:
        pass

    # Tentative 2 : extraire le premier {...} equilibre via regex non-greedy
    match = re.search(r"\{.*\}", texte, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Tentative 3 : nettoyer les fences markdown ```json ... ```
    nettoye = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.MULTILINE)
    try:
        return json.loads(nettoye)
    except json.JSONDecodeError:
        return None


def _normaliser_resultat(brut: dict[str, Any], titre: str) -> dict[str, Any]:
    """
    Valide et nettoie le JSON retourne par Claude.

    Garantit que tous les champs attendus sont presents et dans les bornes,
    quitte a remplacer les valeurs invalides par des defauts.
    """
    defaut = _resultat_par_defaut(titre, raison="normalisation")

    def _clamp(v: Any, mini: int = 0, maxi: int = 10) -> int:
        try:
            return max(mini, min(maxi, int(v)))
        except (TypeError, ValueError):
            return 5

    scores_bruts = brut.get("scores", {}) if isinstance(brut.get("scores"), dict) else {}
    scores = {}
    for dim in ("demande", "douleur", "concurrence", "monetisation"):
        item = scores_bruts.get(dim, {})
        if not isinstance(item, dict):
            item = {}
        scores[dim] = {
            "valeur": _clamp(item.get("valeur")),
            "justification": str(item.get("justification") or "Non justifie.")[:300],
        }

    verdict = str(brut.get("verdict", "")).upper().strip()
    if verdict not in VERDICTS_VALIDES:
        verdict = defaut["verdict"]

    tags = brut.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t)[:50] for t in tags[:3]]

    mots_cles = brut.get("mots_cles_seo", [])
    if not isinstance(mots_cles, list):
        mots_cles = []
    mots_cles = [str(m)[:100] for m in mots_cles[:5]]

    return {
        "resume_fr": str(brut.get("resume_fr") or titre)[:300],
        "tags": tags,
        "niche_detectee": (str(brut["niche_detectee"])[:255]
                           if brut.get("niche_detectee") else None),
        "scores": scores,
        "score_global": _clamp(brut.get("score_global")),
        "verdict": verdict,
        "verdict_raison": str(brut.get("verdict_raison") or "")[:300],
        "mots_cles_seo": mots_cles,
        "risque_ymyl": bool(brut.get("risque_ymyl", False)),
    }


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def analyser(
    titre: str,
    contenu: str,
    donnees: dict | None,
    thematiques: list[str],
    session: AsyncSession | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """
    Analyse un signal FR via Claude et retourne un dict structure.

    Args:
        titre: titre du signal (francais)
        contenu: contenu principal a analyser (francais)
        donnees: donnees brutes optionnelles de la source
        thematiques: liste des thematiques de reference actives
        session: session SQLAlchemy async pour le cache (optionnelle)
        source: nom de la source (reddit, google_trends...) — permet
            l'invalidation ciblee du cache par source

    Returns:
        dict conforme au schema _resultat_par_defaut (toujours valide).
    """
    # Pas de cle API : on retourne directement un resultat neutre
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY non configuree — resultat par defaut")
        return _resultat_par_defaut(titre, raison="cle_api_absente")

    # Verification du cache si session fournie
    hash_contenu = _hash_contenu(titre, contenu, thematiques)
    if session is not None:
        cache_hit = await _lire_cache(session, hash_contenu)
        if cache_hit is not None:
            logger.info(f"Pipeline IA : cache hit pour {hash_contenu[:12]}")
            return cache_hit

    # Appel Claude
    prompt = _construire_prompt(titre, contenu, donnees, thematiques)
    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        reponse = message.content[0].text.strip() if message.content else ""
        logger.info(f"Pipeline IA : reponse Claude ({len(reponse)} chars)")
    except Exception as e:
        logger.error(f"Pipeline IA : erreur appel Claude — {e}")
        return _resultat_par_defaut(titre, raison="erreur_api")

    # Parsing robuste
    brut = _extraire_json(reponse)
    if brut is None:
        logger.warning(f"Pipeline IA : JSON illisible — fallback ({reponse[:200]})")
        return _resultat_par_defaut(titre, raison="json_illisible")

    # Normalisation + ecriture cache
    resultat = _normaliser_resultat(brut, titre)
    if session is not None:
        await _ecrire_cache(session, hash_contenu, resultat, source)

    return resultat
