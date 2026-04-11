"""
Source de signaux : Sitemap Intelligence.

Crawl les sitemaps XML de sites e-commerce / concurrents pour detecter les
nouvelles pages produits publiees recemment. Signal fort qu'une niche est
en train d'etre exploitee activement.

Supporte :
- Sitemap classique (<urlset>) avec <loc> + <lastmod>
- Sitemap index (<sitemapindex>) avec recursion sur les sous-sitemaps
- Compression gzip (extensions .xml.gz ou Content-Type gzip)

Config attendue en base :
    {
        "sitemaps": ["https://example.com/sitemap.xml", ...],
        "max_urls_par_sitemap": 50,   # defaut 50
        "max_age_days": 30,           # defaut 30
        "max_resultats": 30,          # defaut 30 (plafond global)
        "max_index_depth": 2,         # defaut 2
    }

Les URLs sans <lastmod> sont ignorees : sans date on ne peut pas juger
la fraicheur, donc aucun signal exploitable.

NOTE securite : on utilise xml.etree.ElementTree (stdlib). C'est suffisant
pour des sitemaps publics qu'on a nous-memes configures. Pour un usage
non-maitrise (user-submitted URLs), il faudrait passer a defusedxml pour
se proteger des attaques XXE / billion-laughs.
"""

import gzip
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

# Namespace standard des sitemaps XML
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Timeout par requete HTTP (certains sitemaps volumineux prennent du temps)
HTTP_TIMEOUT = 30

# Defauts de config
DEFAULT_MAX_URLS_PAR_SITEMAP = 50
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_RESULTATS = 30
DEFAULT_MAX_INDEX_DEPTH = 2

# User-Agent explicite pour ne pas etre filtre par les WAF
USER_AGENT = "FloouzzBot/0.4 (+https://github.com/cleanup17/floouzz)"


# ---------------------------------------------------------------------------
# Helpers : parsing dates, scoring, extraction du titre
# ---------------------------------------------------------------------------

def _parser_lastmod(lastmod_str: str) -> datetime | None:
    """
    Parse une date ISO 8601 depuis <lastmod>. Tolere les formats les plus
    courants vus dans les sitemaps :
      - 2026-04-11
      - 2026-04-11T14:30:00+00:00
      - 2026-04-11T14:30:00Z
    Retourne None si illisible.
    """
    if not lastmod_str:
        return None

    # Normaliser le 'Z' final en +00:00 pour fromisoformat Python 3.11+
    texte = lastmod_str.strip().replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(texte)
    except ValueError:
        return None

    # Si timezone absente, on assume UTC (par convention pour les sitemaps)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _scorer_fraicheur(age_jours: int) -> int:
    """
    Convertit un age en jours en score 0-100 base sur la fraicheur.
    Plus c'est recent, plus c'est un signal fort qu'une niche est active.
    """
    if age_jours <= 0:
        return 90
    if age_jours <= 3:
        return 80
    if age_jours <= 7:
        return 70
    if age_jours <= 14:
        return 55
    if age_jours <= 30:
        return 40
    return 20  # Hors fenetre : garde un score minimal si on laisse passer


def _extraire_titre_depuis_url(url: str) -> str:
    """
    Extrait un titre lisible depuis une URL produit. On prend le dernier
    segment du path, on remplace les tirets par des espaces, et on tronque.

    Exemple : https://shop.fr/chaussures/baskets-running-trail-max
              -> "baskets running trail max"
    """
    try:
        path = urlparse(url).path.rstrip("/")
        segment = path.split("/")[-1] if path else ""
        # Retirer l'extension eventuelle (.html, .php...)
        if "." in segment:
            segment = segment.rsplit(".", 1)[0]
        titre = segment.replace("-", " ").replace("_", " ").strip()
        return titre[:200] if titre else url[:200]
    except Exception:
        return url[:200]


# ---------------------------------------------------------------------------
# Telechargement + parsing d'un sitemap
# ---------------------------------------------------------------------------

async def _telecharger_sitemap(
    client: httpx.AsyncClient, url: str,
) -> bytes | None:
    """
    Telecharge un sitemap (gere le gzip). Retourne les bytes decompresses
    ou None en cas d'echec.
    """
    try:
        response = await client.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml, */*"},
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"Sitemap telechargement echoue {url} : {e}")
        return None

    contenu = response.content

    # Decompression si c'est du gzip (detecte via l'URL ou le Content-Type)
    content_type = response.headers.get("content-type", "").lower()
    est_gzip = (
        url.endswith(".gz")
        or "gzip" in content_type
        or contenu[:2] == b"\x1f\x8b"  # Magic number gzip
    )
    if est_gzip:
        try:
            contenu = gzip.decompress(contenu)
        except Exception as e:
            logger.warning(f"Sitemap gzip decompression echouee {url} : {e}")
            return None

    return contenu


def _parser_xml(contenu: bytes, source_url: str) -> ET.Element | None:
    """
    Parse un sitemap XML en etree. Retourne la racine ou None si invalide.
    Utilise la stdlib (voir NOTE securite en en-tete du module).
    """
    try:
        return ET.fromstring(contenu)
    except ET.ParseError as e:
        logger.warning(f"Sitemap XML invalide {source_url} : {e}")
        return None


async def _extraire_urls_d_un_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    date_limite: datetime,
    profondeur: int,
    max_index_depth: int,
) -> list[dict]:
    """
    Recupere les URLs fraiches d'un sitemap, avec recursion sur les
    sitemap index (jusqu'a max_index_depth).

    Retourne une liste de dicts {url, lastmod, age_jours}.
    """
    if profondeur > max_index_depth:
        logger.debug(f"Sitemap : profondeur max atteinte pour {sitemap_url}")
        return []

    contenu = await _telecharger_sitemap(client, sitemap_url)
    if contenu is None:
        return []

    racine = _parser_xml(contenu, sitemap_url)
    if racine is None:
        return []

    tag = racine.tag

    # --- Cas 1 : sitemap index (<sitemapindex>) -> recursion -----------------
    if tag.endswith("sitemapindex"):
        sous_urls = []
        for sitemap_elem in racine.findall(f"{SITEMAP_NS}sitemap"):
            loc_elem = sitemap_elem.find(f"{SITEMAP_NS}loc")
            if loc_elem is None or not loc_elem.text:
                continue
            sous_url = loc_elem.text.strip()
            sous_urls.extend(
                await _extraire_urls_d_un_sitemap(
                    client,
                    sous_url,
                    date_limite,
                    profondeur + 1,
                    max_index_depth,
                )
            )
        return sous_urls

    # --- Cas 2 : sitemap classique (<urlset>) --------------------------------
    if not tag.endswith("urlset"):
        logger.warning(f"Sitemap : racine inattendue '{tag}' pour {sitemap_url}")
        return []

    urls_fraiches = []
    maintenant = datetime.now(timezone.utc)

    for url_elem in racine.findall(f"{SITEMAP_NS}url"):
        loc_elem = url_elem.find(f"{SITEMAP_NS}loc")
        lastmod_elem = url_elem.find(f"{SITEMAP_NS}lastmod")

        if loc_elem is None or not loc_elem.text:
            continue
        if lastmod_elem is None or not lastmod_elem.text:
            continue  # Sans lastmod on skip : aucun signal de fraicheur

        lastmod = _parser_lastmod(lastmod_elem.text)
        if lastmod is None:
            continue

        if lastmod < date_limite:
            continue  # Trop vieux

        age_jours = max(0, (maintenant - lastmod).days)
        urls_fraiches.append({
            "url": loc_elem.text.strip(),
            "lastmod": lastmod,
            "age_jours": age_jours,
        })

    return urls_fraiches


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def fetch_sitemap(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Crawl les sitemaps configures et retourne les pages publiees recemment
    comme signaux.

    Args:
        mot_cle: ignore (mode Decouverte : on ecoute ce qui monte, pas de
            recherche par mot-cle). Garde pour respecter le contrat commun.
        config: dict de config de la source (voir docstring du module)

    Returns:
        Liste de SourceResult, triee par fraicheur decroissante, plafonnee
        a config.max_resultats.
    """
    sitemaps = config.get("sitemaps") or []
    if not sitemaps:
        return [SourceResult.error("Aucun sitemap configure (cle 'sitemaps' vide)")]

    max_urls_par_sitemap = config.get("max_urls_par_sitemap", DEFAULT_MAX_URLS_PAR_SITEMAP)
    max_age_days = config.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
    max_resultats = config.get("max_resultats", DEFAULT_MAX_RESULTATS)
    max_index_depth = config.get("max_index_depth", DEFAULT_MAX_INDEX_DEPTH)

    date_limite = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    # Collecte par sitemap, puis fusion
    toutes_urls: list[dict] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for sitemap_url in sitemaps:
            try:
                urls = await _extraire_urls_d_un_sitemap(
                    client,
                    sitemap_url,
                    date_limite,
                    profondeur=1,
                    max_index_depth=max_index_depth,
                )
            except Exception as e:
                logger.error(f"Sitemap {sitemap_url} : erreur inattendue {e}")
                continue

            # On tague chaque URL avec son sitemap d'origine pour la tracabilite
            for u in urls[:max_urls_par_sitemap]:
                u["sitemap_source"] = sitemap_url
                toutes_urls.append(u)

    if not toutes_urls:
        return [SourceResult(
            titre="Sitemap : aucune URL fraiche detectee",
            donnees={
                "sitemaps": sitemaps,
                "max_age_days": max_age_days,
                "collecte": datetime.now(timezone.utc).isoformat(),
            },
            score_partiel=0,
        )]

    # Tri global par fraicheur (plus recent en premier) + plafond
    toutes_urls.sort(key=lambda x: x["lastmod"], reverse=True)
    toutes_urls = toutes_urls[:max_resultats]

    results = []
    for item in toutes_urls:
        url = item["url"]
        age = item["age_jours"]
        titre_url = _extraire_titre_depuis_url(url)
        score = _scorer_fraicheur(age)

        # Domaine extrait pour affichage dans le titre
        try:
            domaine = urlparse(url).netloc
        except Exception:
            domaine = "inconnu"

        titre = f"{domaine} : {titre_url}" if titre_url else f"Nouvelle page {domaine}"

        results.append(SourceResult(
            titre=titre[:300],
            url=url,
            donnees={
                "source": "sitemap",
                "domaine": domaine,
                "sitemap_source": item["sitemap_source"],
                "lastmod": item["lastmod"].isoformat(),
                "age_jours": age,
                "collecte": datetime.now(timezone.utc).isoformat(),
            },
            score_partiel=score,
        ))

    return results
