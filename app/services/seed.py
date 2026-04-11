"""Insertion des sources et thematiques par defaut au premier demarrage."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, Thematique

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thematiques de reference (40+ categories FR avec demande prouvee)
# ---------------------------------------------------------------------------
#
# Ces thematiques servent de vocabulaire commun pour :
#   - le tagging des decouvertes par pipeline_ia
#   - les filtres du dashboard Decouverte
#   - le prompt pipeline_ia (liste envoyee a Claude comme contexte)
#
# Convention de nommage : sans accents (aligne sur les thematiques existantes
# en base pour eviter les doublons type 'Sante' / 'Sante'). La contrainte
# UNIQUE sur Thematique.nom garantit l'unicite cote BDD.
#
# Liste idempotente via seed_thematiques_manquantes() : seules les entrees
# absentes sont inserees a chaque demarrage. Les thematiques existantes
# (rajoutees manuellement par l'utilisatrice) sont preservees.
DEFAULT_THEMATIQUES = [
    # --- Core business / numerique ---
    "IA",
    "SaaS",
    "E-commerce",
    "Marketing",
    "SEO",
    "Automatisation",
    "Innovation",
    "Creation",
    "Video",
    # --- Metiers & parcours ---
    "Metiers & RH",
    "Emploi",
    "Formation",
    "Education",
    "Developpement personnel",
    # --- Sante & bien-etre ---
    "Sante",
    "Bien-etre",
    "Nutrition",
    "Fitness",
    "Yoga",
    "Beaute",
    # --- Maison & quotidien ---
    "Maison & Bricolage",
    "Jardin",
    "Decoration",
    "Cuisine",
    "Electromenager",
    # --- Famille ---
    "Enfants & Bebe",
    "Animaux",
    "Seniors",
    "Etudiants",
    # --- Mobilite ---
    "Auto",
    "Moto",
    "Vehicules",
    "Nautisme",
    # --- Sport & plein air ---
    "Sport",
    "Randonnee",
    "Chasse & Peche",
    "Equitation",
    # --- Tech & loisirs numeriques ---
    "Informatique",
    "High-tech",
    "Jeux video",
    # --- Finance & immobilier ---
    "Finance",
    "Immobilier",
    # --- Voyage & tourisme ---
    "Voyage",
    "Tourisme",
    # --- Culture & loisirs ---
    "Mode",
    "Livres & Culture",
    "Musique",
    "Photo",
    "Artisanat",
    "Collectionneurs",
    # --- Ecologie ---
    "Ecologie",
]

DEFAULT_SOURCES = [
    # --- Gratuit (sans cle) ---
    {
        "nom": "Hacker News — posts populaires",
        "type": "api",
        "config": {"fetcher": "hackernews"},
        "cle_api_ref": None,
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- Google Trends RSS (gratuit, remplace SerpAPI trending par defaut) ---
    {
        "nom": "Google Trends RSS — FR",
        "type": "api",
        "config": {
            "fetcher": "google_trends_rss",
            "geo": "FR",
            "max_items": 10,
            "filtre_bruit": True,
        },
        "cle_api_ref": None,
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- SerpAPI (1 appel chacune) ---
    # Source desactivee par defaut : remplacee par Google Trends RSS ci-dessus
    # (gratuit, zero quota). Reactivable si tu veux les 'articles_associes' en
    # bonus via SerpAPI. Decision v0.5.3 (Dedup-3).
    {
        "nom": "Google Trends — ce qui monte en France",
        "type": "serpapi",
        "config": {"engine": "google_trends", "gl": "FR", "hl": "fr"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": False,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News — actu tech",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr", "topic": "TECHNOLOGY"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google News — actu business",
        "type": "serpapi",
        "config": {"engine": "google_news", "gl": "fr", "hl": "fr", "topic": "BUSINESS"},
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Google Jobs — metiers qui recrutent",
        "type": "serpapi",
        "config": {
            "engine": "google_jobs", "gl": "fr", "hl": "fr",
            "queries": ["developpeur IA", "automatisation", "e-commerce manager", "data analyst", "no-code"],
        },
        "cle_api_ref": "SERPAPI_KEY",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- Apify ---
    {
        "nom": "Reddit — posts chauds communautes tech/business",
        "type": "apify_actor",
        "config": {
            "actor_id": "trudax/reddit-scraper",
            "input": {
                "subreddits": ["SaaS", "smallbusiness", "startups", "Entrepreneur", "artificial", "ecommerce", "nocode"],
                "sort": "hot",
                "time": "week",
                "maxItems": 50,
            },
        },
        "cle_api_ref": "APIFY_TOKEN",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    {
        "nom": "Product Hunt — produits du jour",
        "type": "apify_actor",
        "config": {
            "actor_id": "dainty_screw/producthunt-scraper",
            "fetcher": "producthunt",
            "input": {"listType": "today", "maxItems": 15},
        },
        "cle_api_ref": "APIFY_TOKEN",
        "actif": True,
        "cron_expr": "0 6 * * *",
    },
    # --- Sitemap Intelligence (v0.4+) ---
    # Crawl les sitemaps XML de sites concurrents pour detecter les nouvelles
    # pages produits. Signal fort qu'une niche est exploitee activement.
    # Desactive par defaut : a configurer + activer manuellement dans l'admin.
    # Les URLs "REMPLACER-*" sont des placeholders visibles dans l'UI admin :
    # l'utilisatrice doit les editer avant d'activer la source.
    {
        "nom": "Sitemap Intelligence",
        "type": "sitemap",
        "config": {
            "sitemaps": [
                "https://REMPLACER-PAR-CONCURRENT-1.com/sitemap.xml",
                "https://REMPLACER-PAR-CONCURRENT-2.fr/sitemap_index.xml.gz",
            ],
            "max_urls_par_sitemap": 50,
            "max_age_days": 30,
            "max_resultats": 30,
            "max_index_depth": 2,
        },
        "cle_api_ref": None,
        "actif": False,
        "cron_expr": "0 6 * * *",
    },
]


async def seed_sources_manquantes(db: AsyncSession) -> int:
    """
    Insere les sources de DEFAULT_SOURCES qui ne sont pas encore en base,
    identifiees par leur nom. Idempotent : peut etre appele a chaque
    demarrage sans creer de doublon.

    Utile quand on ajoute une nouvelle source par defaut apres le seed
    initial (ex: Sitemap Intelligence en v0.4+).
    """
    stmt = select(Source.nom)
    result = await db.execute(stmt)
    noms_existants = {row[0] for row in result.all()}

    count = 0
    for s in DEFAULT_SOURCES:
        if s["nom"] in noms_existants:
            continue
        db.add(Source(**s))
        count += 1

    if count:
        await db.commit()
        logger.info(f"Seed : {count} nouvelle(s) source(s) ajoutee(s)")
    return count


async def seed_thematiques_manquantes(db: AsyncSession) -> int:
    """
    Insere les thematiques de DEFAULT_THEMATIQUES qui ne sont pas encore en
    base, identifiees par leur nom (contrainte UNIQUE sur Thematique.nom).
    Idempotent : appelable a chaque demarrage sans creer de doublon.

    Preserve les thematiques custom que l'utilisatrice a rajoutees manuellement
    via l'UI /parametres/ — on n'efface rien, on ajoute seulement les
    manquantes.
    """
    stmt = select(Thematique.nom)
    result = await db.execute(stmt)
    noms_existants = {row[0] for row in result.all()}

    count = 0
    for nom in DEFAULT_THEMATIQUES:
        if nom in noms_existants:
            continue
        db.add(Thematique(nom=nom, actif=True))
        count += 1

    if count:
        await db.commit()
        logger.info(f"Seed : {count} nouvelle(s) thematique(s) ajoutee(s)")
    return count


async def seed_sources_par_defaut(db: AsyncSession) -> int:
    """Insere les sources par defaut si la table est vide."""
    stmt = select(Source).limit(1)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        logger.info("Sources deja presentes — seed ignore")
        return 0

    count = 0
    for s in DEFAULT_SOURCES:
        source = Source(**s)
        db.add(source)
        count += 1

    await db.commit()
    logger.info(f"Seed : {count} sources par defaut inserees")
    return count
