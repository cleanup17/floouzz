"""
Source de signaux : Product Hunt via flux RSS Atom public (gratuit, sans cle).

Utilise le flux Atom public https://www.producthunt.com/feed pour recuperer
les produits recemment lances. Pas de votes disponibles dans le RSS, mais les
produits lances y sont tous listes — c'est un signal de "nouveaute" pur.

Remplace l'ancien connecteur Apify (dainty_screw/producthunt-scraper, 404
depuis avril 2026). Le flux RSS est gratuit, sans cle, sans quota.

Les taglines (descriptions courtes) sont pre-traduites EN->FR via
traduire_titres() avant transmission au scanner / pipeline_ia.
"""

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

import httpx

from app.services.sources.base import SourceResult
from app.services.traduction import traduire_titres

logger = logging.getLogger(__name__)

# URL du flux Atom Product Hunt (public, ~50 entrees, ~44 Ko)
PH_FEED_URL = "https://www.producthunt.com/feed"

# Namespace Atom
ATOM_NS = "{http://www.w3.org/2005/Atom}"

# User-Agent
USER_AGENT = "FloouzzBot/0.5 (+https://github.com/cleanup17/floouzz)"

# Nombre max de produits a retourner
DEFAULT_MAX_ITEMS = 15


def _extraire_description(content_html: str | None) -> str:
    """
    Extrait la description courte depuis le HTML du champ <content>.
    Product Hunt met le texte dans le premier <p> du HTML.
    On retire les balises HTML et on decode les entites.
    """
    if not content_html:
        return ""
    # Extraire le texte du premier <p>
    match = re.search(r"<p>(.*?)</p>", content_html, re.DOTALL)
    if not match:
        return ""
    texte = match.group(1).strip()
    # Retirer les balises HTML restantes
    texte = re.sub(r"<[^>]+>", "", texte)
    # Decoder les entites HTML
    texte = unescape(texte).strip()
    return texte


def _parser_date_atom(date_str: str | None) -> str | None:
    """Parse une date Atom ISO 8601 en ISO UTC. None si illisible."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


async def fetch_producthunt(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les produits recemment lances via le flux
    RSS Atom public Product Hunt (gratuit, sans cle).

    Le mot_cle est ignore (mode ecoute, pas de recherche par mot-cle).

    Config supportee :
        - input.maxItems : int (defaut DEFAULT_MAX_ITEMS)

    Note : le flux RSS ne contient pas les votes. Tous les produits sont
    scores a 50 (neutre) — c'est pipeline_ia qui jugera la pertinence
    business via le resume et le contexte.
    """
    max_items = DEFAULT_MAX_ITEMS
    if "input" in config and isinstance(config["input"], dict):
        max_items = config["input"].get("maxItems", DEFAULT_MAX_ITEMS)

    # Telechargement du flux Atom
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                PH_FEED_URL,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Product Hunt RSS erreur : {e}")
        return [SourceResult.error(str(e))]

    # Parsing XML Atom
    try:
        racine = ET.fromstring(response.content)
    except ET.ParseError as e:
        logger.warning(f"Product Hunt RSS : XML invalide — {e}")
        return [SourceResult.error(f"XML invalide : {e}")]

    # Extraction des entrees
    entries = racine.findall(f"{ATOM_NS}entry")
    if not entries:
        return []

    entries = entries[:max_items]

    # Extraction des donnees brutes
    produits: list[dict] = []
    for entry in entries:
        title_elem = entry.find(f"{ATOM_NS}title")
        link_elem = entry.find(f"{ATOM_NS}link")
        content_elem = entry.find(f"{ATOM_NS}content")
        published_elem = entry.find(f"{ATOM_NS}published")

        nom = (title_elem.text or "").strip() if title_elem is not None else ""
        if not nom:
            continue

        url = ""
        if link_elem is not None:
            url = link_elem.get("href", "").strip()

        content_html = (content_elem.text or "") if content_elem is not None else ""
        tagline = _extraire_description(content_html)
        published = ""
        if published_elem is not None:
            published = (published_elem.text or "").strip()

        produits.append({
            "nom": nom,
            "tagline_en": tagline,
            "url": url,
            "published": published,
        })

    if not produits:
        return []

    # Traduction batch EN->FR des taglines (les noms de produits sont des
    # marques, on ne les traduit pas — que les descriptifs).
    taglines_en = [p["tagline_en"] for p in produits]
    taglines_fr = await traduire_titres(taglines_en)

    results = []
    for p, tagline_fr in zip(produits, taglines_fr):
        nom = p["nom"]
        pub_date = _parser_date_atom(p["published"])

        # Score neutre 50 : le flux RSS n'a pas les votes, on ne peut pas
        # scorer par popularite. C'est pipeline_ia qui jugera la pertinence
        # business dans l'etape enrichissement.
        results.append(SourceResult(
            titre=f"PH : {nom} — {tagline_fr}" if tagline_fr else f"PH : {nom}",
            url=p["url"],
            donnees={
                "nom": nom,
                "tagline_original": p["tagline_en"],
                "tagline_fr": tagline_fr,
                "published": pub_date,
                "source": "producthunt",
                "collecte": datetime.now(timezone.utc).isoformat(),
            },
            score_partiel=50,
        ))

    return results
