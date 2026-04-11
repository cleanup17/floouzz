"""
Source de signaux : Google Trends RSS — tendances quotidiennes FR (gratuit).

Recupere le flux public RSS des tendances Google Trends pour un pays donne
(defaut : FR). Pas de cle API, pas de quota, signal precoce sur les
tendances montantes du jour.

URL : https://trends.google.com/trending/rss?geo=FR (~3 Ko, 10 items max)

Chaque item contient :
  - title             : terme en tendance (deja en francais pour geo=FR)
  - ht:approx_traffic : volume approximatif ("200+", "500+", "1000+"...)
  - pubDate           : horodatage RFC 822
  - link              : URL du flux lui-meme (pas du terme) — on construit
                        nous-memes une URL /trends/explore cliquable

Filtrage en deux temps :
1. Blacklist patterns (option A) : elimine le bruit actu evident
   (sport/TV/celebrites/vacances) avant enrichissement pipeline_ia.
2. Claude via pipeline_ia (option C) : juge la pertinence business au
   moment du scan, les items sans valeur sont marques `ignore`.

Seuil de trafic minimum : 500+ pour filtrer les micro-pics trop faibles.

NOTE securite : on utilise xml.etree.ElementTree (stdlib). C'est suffisant
pour un flux public Google. Pour un usage non-maitrise, passer a defusedxml.
"""

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from app.services.sources.base import SourceResult

logger = logging.getLogger(__name__)

# URL du flux RSS (validee le 2026-04-11 sur https://trends.google.com/trending/rss?geo=FR)
URL_TEMPLATE = "https://trends.google.com/trending/rss?geo={geo}"

# Namespace Google Trends custom declare dans le flux RSS
HT_NS = "{https://trends.google.com/trending/rss}"

# Timeout court : le flux est leger (~3 Ko)
HTTP_TIMEOUT = 15

# User-Agent explicite
USER_AGENT = "FloouzzBot/0.5 (+https://github.com/cleanup17/floouzz)"

# Mapping approx_traffic (enum discret cote Google) vers score_partiel 0-100
TRAFFIC_TO_SCORE = {
    "10000+": 90,
    "5000+":  80,
    "2000+":  70,
    "1000+":  60,
    "500+":   45,
    "200+":   30,
    "100+":   20,
}

# Seuil de volume minimum pour garder un item (filtre les micro-pics)
VOLUME_MIN = 500

# Valeurs par defaut (surchargeables via config de source)
DEFAULT_GEO = "FR"
DEFAULT_MAX_ITEMS = 10


# ---------------------------------------------------------------------------
# Blacklist patterns — pre-filtre "bruit actualite chaude"
# ---------------------------------------------------------------------------
#
# Liste maintenable manuellement. Le but n'est PAS d'etre exhaustive : on
# laisse pipeline_ia (Claude) juger en second niveau. On elimine juste
# l'evident pour economiser des appels Claude sur du bruit certain.
PATTERNS_BRUIT = [
    # Sport (clubs, tournois, evenements recurrents)
    re.compile(r"\b(ubb|psg|om|ol|asse|rcs|rcl)\b", re.IGNORECASE),
    re.compile(r"(monte[- ]carlo|roland[- ]?garros|tour de france)", re.IGNORECASE),
    re.compile(r"\b(ligue 1|ligue 2|top 14|pro d2|euro 2024|euro 2028)\b", re.IGNORECASE),
    re.compile(r"\b(atp|wta|nba|f1|formule 1|rugby|tennis)\b", re.IGNORECASE),
    # Emissions TV recurrentes
    re.compile(
        r"(koh lanta|danse avec|n['’]oubliez pas|star academy|top chef|"
        r"the voice|masterchef|qui veut|pekin express)",
        re.IGNORECASE,
    ),
    # Evenements saisonniers grand public
    re.compile(r"\b(eurovision|oscars|cesars|miss france|cannes)\b", re.IGNORECASE),
    # Destinations touristiques generiques
    re.compile(r"\b(majorque|ibiza|canaries|baleares|marrakech)\b", re.IGNORECASE),
    # Meteo / catastrophes naturelles (signal ephemere)
    re.compile(r"\b(tempete|ouragan|inondation|canicule|seisme)\b", re.IGNORECASE),
]


def _est_bruit(titre: str) -> bool:
    """
    True si le titre matche un pattern de la blacklist.
    Appel case-insensitive, chaque pattern est precompile.
    """
    if not titre:
        return False
    for pattern in PATTERNS_BRUIT:
        if pattern.search(titre):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers purs
# ---------------------------------------------------------------------------

def _parser_traffic(traffic_str: str | None) -> int:
    """
    Parse '500+' / '1000+' / '10000+' en int (500, 1000, 10000).
    Retourne 0 si illisible.
    """
    if not traffic_str:
        return 0
    match = re.match(r"(\d+)", traffic_str.strip())
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (ValueError, TypeError):
        return 0


def _parser_pub_date(date_str: str | None) -> str | None:
    """
    Parse une date RFC 822 ('Sat, 11 Apr 2026 05:10:00 -0700') en ISO 8601 UTC.
    Retourne None si illisible.
    """
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _construire_url_explore(terme: str, geo: str) -> str:
    """
    Construit une URL cliquable vers la page Google Trends du terme.
    Le <link> du flux RSS pointe vers le flux lui-meme (inutile pour l'UI).
    """
    terme_encode = quote_plus(terme)
    return f"https://trends.google.com/trends/explore?q={terme_encode}&geo={geo}"


def _dedup_semantique(items: list[dict]) -> list[dict]:
    """
    Deduplication sous-chaine : si un titre A est sous-chaine d'un titre B
    plus long, on garde B (plus specifique) et on jette A.

    Exemple : 'zverev' est sous-chaine de 'alexander zverev' -> on garde
    'alexander zverev' uniquement.

    On itere du plus long au plus court : quand un titre court arrive, il
    est compare aux titres deja gardes (plus longs) ; s'il est sous-chaine
    de l'un d'eux, il est absorbe.
    """
    if not items:
        return []

    # Tri par longueur DECROISSANTE : les plus longs sont traites en premier
    # et servent d'absorbeurs pour les sous-chaines qui arrivent apres.
    items_tries = sorted(
        items, key=lambda x: len(x.get("titre", "")), reverse=True,
    )
    gardes: list[dict] = []

    for courant in items_tries:
        titre_courant = (courant.get("titre") or "").strip().lower()
        if not titre_courant:
            continue

        # Si le titre courant est sous-chaine d'un titre deja garde
        # (plus long), on l'absorbe et on skippe.
        absorbe = False
        for existant in gardes:
            titre_existant = (existant.get("titre") or "").strip().lower()
            if titre_courant != titre_existant and titre_courant in titre_existant:
                absorbe = True
                break
        if not absorbe:
            gardes.append(courant)

    # On conserve l'ordre original (par volume) pour la sortie finale
    titres_gardes = {(g.get("titre") or "").strip().lower() for g in gardes}
    return [
        item for item in items
        if (item.get("titre") or "").strip().lower() in titres_gardes
    ]


# ---------------------------------------------------------------------------
# Telechargement + parsing XML
# ---------------------------------------------------------------------------

async def _telecharger_rss(url: str) -> bytes | None:
    """
    Telecharge le flux RSS. Retourne les bytes bruts ou None sur erreur.
    Pas de gzip attendu (le flux est deja tres leger).
    """
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/xml, text/xml",
                },
                follow_redirects=True,
            )
            response.raise_for_status()
    except Exception as e:
        logger.warning(f"Google Trends RSS : telechargement echoue {url} — {e}")
        return None

    return response.content


def _parser_rss(contenu: bytes) -> list[dict]:
    """
    Parse le flux RSS et extrait une liste de dicts par item.

    Chaque dict contient :
      - titre              : str (brut, deja lowercase dans le flux)
      - approx_traffic     : str ('500+', '1000+'...)
      - volume_min         : int (500, 1000...)
      - pub_date           : str ISO 8601 UTC ou None

    Retourne une liste vide si XML invalide.
    """
    try:
        racine = ET.fromstring(contenu)
    except ET.ParseError as e:
        logger.warning(f"Google Trends RSS : XML invalide — {e}")
        return []

    channel = racine.find("channel")
    if channel is None:
        logger.warning("Google Trends RSS : element <channel> absent")
        return []

    items: list[dict] = []
    for item_elem in channel.findall("item"):
        titre_elem = item_elem.find("title")
        traffic_elem = item_elem.find(f"{HT_NS}approx_traffic")
        pubdate_elem = item_elem.find("pubDate")

        titre = (titre_elem.text or "").strip() if titre_elem is not None else ""
        if not titre:
            continue

        traffic_str = (traffic_elem.text or "").strip() if traffic_elem is not None else ""
        pub_date_str = (pubdate_elem.text or "").strip() if pubdate_elem is not None else ""

        items.append({
            "titre": titre,
            "approx_traffic": traffic_str,
            "volume_min": _parser_traffic(traffic_str),
            "pub_date": _parser_pub_date(pub_date_str),
        })

    return items


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

async def fetch_google_trends_rss(
    mot_cle: str, config: dict,
) -> list[SourceResult]:
    """
    Mode Decouverte : recupere les tendances Google Trends du jour via RSS.

    Le mot_cle est ignore (mode ecoute, pas de recherche par mot-cle).
    Garde la signature pour respecter le contrat commun des sources.

    Config supportee :
        - geo         : str (defaut 'FR')
        - max_items   : int (defaut 10)
        - filtre_bruit: bool (defaut True) — applique la blacklist patterns

    Retourne une liste de SourceResult filtree :
      - items avec volume_min < VOLUME_MIN elimines
      - items blackliste elimines (si filtre_bruit=True)
      - deduplication sous-chaine appliquee
      - tri final par volume_min decroissant
    """
    geo = config.get("geo", DEFAULT_GEO)
    max_items = config.get("max_items", DEFAULT_MAX_ITEMS)
    filtre_bruit = config.get("filtre_bruit", True)

    url = URL_TEMPLATE.format(geo=geo)

    contenu = await _telecharger_rss(url)
    if contenu is None:
        return [SourceResult.error(f"Telechargement RSS echoue pour geo={geo}")]

    items_bruts = _parser_rss(contenu)
    if not items_bruts:
        return [SourceResult(
            titre=f"Google Trends RSS : aucun item parse (geo={geo})",
            donnees={"geo": geo, "collecte": datetime.now(timezone.utc).isoformat()},
            score_partiel=0,
        )]

    # Etape 1 : filtre volume minimum
    items_filtres = [
        item for item in items_bruts
        if item.get("volume_min", 0) >= VOLUME_MIN
    ]

    # Etape 2 : filtre blacklist "bruit"
    if filtre_bruit:
        items_filtres = [
            item for item in items_filtres
            if not _est_bruit(item.get("titre", ""))
        ]

    # Etape 3 : deduplication sous-chaine
    items_filtres = _dedup_semantique(items_filtres)

    # Etape 4 : tri par volume decroissant + plafond max_items
    items_filtres.sort(key=lambda x: x.get("volume_min", 0), reverse=True)
    items_filtres = items_filtres[:max_items]

    if not items_filtres:
        logger.info(
            f"Google Trends RSS : aucun item exploitable apres filtrage "
            f"(geo={geo}, bruit={filtre_bruit})"
        )
        return []

    # Etape 5 : construction des SourceResult
    results: list[SourceResult] = []
    maintenant = datetime.now(timezone.utc).isoformat()

    for item in items_filtres:
        terme = item["titre"]
        traffic_str = item["approx_traffic"] or ""
        score = TRAFFIC_TO_SCORE.get(traffic_str, 30)

        results.append(SourceResult(
            titre=f"Tendance FR : {terme}",
            url=_construire_url_explore(terme, geo),
            donnees={
                "source": "google_trends_rss",
                "terme": terme,
                "approx_traffic": traffic_str,
                "volume_min": item["volume_min"],
                "pub_date": item["pub_date"],
                "geo": geo,
                "collecte": maintenant,
            },
            score_partiel=score,
        ))

    logger.info(
        f"Google Trends RSS : {len(results)} tendance(s) retenue(s) "
        f"(geo={geo}, apres filtrage)"
    )
    return results
