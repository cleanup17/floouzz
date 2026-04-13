# Floouzz

Outil de recherche et veille de niches de marche. Collecte des signaux multi-sources, les enrichit via IA, et produit des fiches niches scorees et actionnables.

## Fonctionnalites

### Mode Decouverte
Dashboard de signaux quotidiens collectes depuis 7 sources actives :
- **Hacker News** — posts populaires (API Algolia, gratuit)
- **Reddit** — posts chauds de 7 subreddits tech/business (API native, gratuit)
- **Product Hunt** — produits lances du jour (flux RSS Atom, gratuit)
- **Google Trends RSS** — tendances de recherche FR (flux RSS, gratuit)
- **Google News** — actualites tech et business (SerpAPI)
- **Google Jobs** — metiers qui recrutent (SerpAPI)
- **Sitemap Intelligence** — nouvelles pages de sites concurrents (configurable)

### Mode Analyse
Analyse complete d'une niche en un clic, via 7 services paralleles + 2 sequentiels :

| Service | Signal | Source | Cout |
|---------|--------|--------|------|
| **Pipeline IA** | Score business 4 dimensions + verdict GO/WATCH/SKIP | Claude Sonnet 4.5 | ~$0.010 |
| **SERP Gap** | Difficulte SEO + signaux enrichis + pub active | SerpAPI + Claude | ~$0.014 |
| **Affiliate Finder** | Programmes d'affiliation detectes | SerpAPI + Claude | ~$0.016 |
| **Saisonnalite** | Cycles Google Trends 12 mois | SerpAPI | ~$0.002 |
| **Marketplace Gap** | Vendeurs Etsy/Rakuten/eBay | SerpAPI | ~$0.006 |
| **Amazon Market** | Volume produits, prix moyen, top ASINs | SerpAPI | ~$0.002 |
| **International** | Potentiel 4 marches (US/DE/ES/IT) | Claude Haiku + SerpAPI | ~$0.010 |
| **Expand Keywords** | 15-20 variantes longue traine par intention | Claude Haiku | ~$0.002 |
| **Keyword Clustering** | 5-7 sous-niches testables | Claude Haiku | ~$0.002 |

**Cout total par analyse** : ~$0.064 (gratuit en cache hit)

### Autres fonctionnalites
- Export fiche niche en **Markdown** telechargeable
- **Historique** des analyses avec fiches consultables
- **51 thematiques** de tagging (seed idempotent)
- **Cache IA multi-TTL** : 24h (pipeline_ia), 7j (serp_gap), 30j (affiliation, saisonnalite, marketplace, amazon, international)
- **Scheduler APScheduler** pour scan quotidien automatique
- **Deduplication** des signaux (niche_detectee, tags, similarite titre)
- Interface **HTMX + Tailwind CSS** sans SPA

## Stack technique

| Couche | Technologie |
|--------|------------|
| Backend | Python 3.13 + FastAPI |
| Frontend | HTMX + Tailwind CSS (CDN) + Jinja2 |
| Base de donnees | PostgreSQL 16 |
| Infrastructure | Docker Compose |
| IA | Claude API (Sonnet 4.5 + Haiku 4.5) |
| Sources | SerpAPI, Reddit API, Product Hunt RSS, HN Algolia, Google Trends RSS |
| Scheduler | APScheduler (AsyncIOScheduler) |

## Installation

### Prerequis
- Docker + Docker Compose
- Cles API (voir section Variables d'environnement)

### Demarrage

```bash
# Cloner le repo
git clone https://github.com/cleanup17/floouzz.git
cd floouzz

# Copier et configurer les variables d'environnement
cp .env.example .env
# Editer .env avec vos cles API

# Lancer les conteneurs
docker compose up -d

# Appliquer les migrations
docker compose exec db psql -U floouzz -d floouzz -f /docker-entrypoint-initdb.d/01-init.sql
# Les migrations phase2 a phase13 s'appliquent via stdin :
for f in migrations/phase*.sql; do
    docker compose exec -T db psql -U floouzz -d floouzz < "$f"
done

# L'application est accessible sur http://localhost:8001
```

### Rebuild apres modification

```bash
docker compose build app
docker compose up -d app
```

## Variables d'environnement

### Obligatoires

| Variable | Description |
|----------|------------|
| `DATABASE_URL` | URL PostgreSQL (defaut : `postgresql+asyncpg://floouzz:floouzz_secret@db:5432/floouzz`) |
| `POSTGRES_USER` | Utilisateur PostgreSQL (defaut : `floouzz`) |
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL |
| `POSTGRES_DB` | Nom de la base (defaut : `floouzz`) |

### API (optionnelles mais recommandees)

| Variable | Description | Gratuit ? |
|----------|------------|-----------|
| `ANTHROPIC_API_KEY` | Cle API Claude (Anthropic) — enrichissement IA | Non (pay-per-use) |
| `SERPAPI_KEY` | Cle SerpAPI — Google Trends, SERP, Amazon | Non (plan a partir de $75/mois) |

### Optionnelles

| Variable | Description | Defaut |
|----------|------------|--------|
| `SCAN_CRON` | Expression cron du scan quotidien (5 champs) | `0 6 * * *` |
| `WEBHOOK_TOKEN` | Token pour securiser l'endpoint webhook n8n | vide |
| `APP_DEBUG` | Mode debug FastAPI | `true` |

## Commandes utiles

```bash
# Lancer les tests (108 tests)
docker compose exec app python -m pytest tests/ -v

# Lancer un scan manuel
# Via l'UI : cliquer "Rafraichir" sur /decouverte/

# Verifier l'etat des sources
docker compose exec -T db psql -U floouzz -d floouzz -c \
    "SELECT nom, type, actif FROM sources ORDER BY actif DESC, nom;"

# Purger le cache d'un service specifique
docker compose exec -T db psql -U floouzz -d floouzz -c \
    "DELETE FROM cache_ia WHERE source = 'serp_gap';"

# Voir les logs du scanner
docker compose logs app | grep -i "scanner\|enrichi\|erreur"

# Desactiver le scheduler automatique
# Ajouter SCAN_CRON= (vide) dans .env puis restart

# Tester un connecteur source en isolation
docker compose exec app python -m scripts.test_sitemap_scan
docker compose exec app python -m scripts.test_serp_gap "mot cle"
docker compose exec app python -m scripts.test_affiliate_finder "mot cle"
docker compose exec app python -m scripts.test_saisonnalite "mot cle"
```

## Structure du projet

```
floouzz/
├── app/
│   ├── main.py                 # Point d'entree FastAPI + scheduler
│   ├── config.py               # Settings via pydantic-settings
│   ├── database.py             # Engine + session async SQLAlchemy
│   ├── models.py               # Modeles ORM (7 colonnes JSONB d'analyse)
│   ├── schemas.py              # Schemas Pydantic (validation)
│   ├── routers/
│   │   ├── niches.py           # Mode Analyse (/analyser, /analyse/{id})
│   │   ├── decouvertes.py      # Mode Decouverte (/decouverte/)
│   │   ├── exports.py          # Export Markdown
│   │   ├── sources.py          # CRUD admin sources
│   │   ├── parametres.py       # Page parametres
│   │   └── webhooks.py         # Endpoint webhook n8n
│   ├── services/
│   │   ├── pipeline_ia.py      # Enrichissement business (Claude Sonnet)
│   │   ├── serp_gap.py         # Analyse SEO concurrentielle
│   │   ├── affiliate_finder.py # Detection programmes affiliation
│   │   ├── saisonnalite.py     # Cycles Google Trends 12 mois
│   │   ├── marketplace_gap.py  # Vendeurs Etsy/Rakuten/eBay
│   │   ├── amazon_market.py    # Volume produits Amazon FR
│   │   ├── international.py    # Potentiel 4 marches internationaux
│   │   ├── expand_keywords.py  # Variantes longue traine par intention
│   │   ├── keyword_clustering.py # Sous-niches testables
│   │   ├── scanner.py          # Orchestrateur scan quotidien
│   │   ├── deduplication.py    # Detection doublons 7 jours
│   │   ├── traduction.py       # Traduction EN->FR (Claude Haiku)
│   │   └── seed.py             # Sources + thematiques par defaut
│   ├── services/sources/       # Connecteurs de collecte
│   │   ├── base.py             # Interface commune + registre fetchers
│   │   ├── google_trends.py    # SerpAPI Google Trends
│   │   ├── google_trends_rss.py # RSS Google Trends (gratuit)
│   │   ├── google_jobs.py      # SerpAPI Google Jobs
│   │   ├── google_news.py      # SerpAPI Google News
│   │   ├── google_search.py    # SerpAPI Google Search
│   │   ├── reddit.py           # API native Reddit (gratuit)
│   │   ├── producthunt.py      # RSS Atom Product Hunt (gratuit)
│   │   ├── hackernews.py       # API Algolia HN (gratuit)
│   │   ├── sitemap.py          # Crawl sitemaps XML concurrents
│   │   └── apify_url.py        # Scraper URL generique Apify
│   ├── templates/              # Templates Jinja2 + HTMX
│   └── static/css/app.css      # Styles complementaires
├── migrations/                 # SQL phase1 a phase13
├── scripts/                    # Scripts de test dry-run
├── tests/                      # 108 tests pytest
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── docs/
    ├── architecture.md
    ├── changelog.md
    └── dette-technique.md
```

## Documentation

- [Architecture technique](docs/architecture.md)
- [Changelog](docs/changelog.md)
- [Dette technique](docs/dette-technique.md)

## Licence

Projet prive — usage personnel uniquement.
