# Floouzz — Architecture technique

## Vue d'ensemble

Floouzz est un outil de recherche et veille de niches de marche. Il collecte des signaux multi-sources, les stocke en base, et produit une fiche niche scoree et actionnable.

## Stack technique

| Couche | Technologie | Justification |
|--------|------------|---------------|
| Backend | Python 3.13 + FastAPI | Async natif, performant, ecosysteme data riche |
| Frontend | HTMX + Tailwind CSS (CDN) | Interactions dynamiques sans SPA, styling rapide |
| Base de donnees | PostgreSQL 16 | JSONB pour signaux, robuste, prepare le SaaS |
| Infrastructure | Docker Compose | Dev local = prod (Synology ou VPS) |

## Architecture applicative

```
floouzz/
├── app/
│   ├── main.py              # Point d'entree FastAPI
│   ├── config.py             # Settings via pydantic-settings
│   ├── database.py           # Engine + session async SQLAlchemy
│   ├── models.py             # Modeles ORM (Niche, Analyse, Signal)
│   ├── schemas.py            # Schemas Pydantic (validation)
│   ├── routers/
│   │   └── niches.py         # Routes : accueil, analyser, historique
│   ├── services/
│   │   ├── scoring.py        # Calcul score global + verdict
│   │   └── sources/
│   │       └── google_trends.py  # Collecte Google Trends (pytrends)
│   ├── templates/            # Templates Jinja2 + HTMX
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── historique.html
│   │   └── partials/
│   │       ├── fiche.html    # Fragment fiche niche (HTMX)
│   │       └── erreur.html
│   └── static/css/app.css
├── migrations/
│   └── init.sql              # Schema PostgreSQL initial
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Decisions techniques

### 1. PostgreSQL des la V1 (pas SQLite)
- Le champ `donnees JSONB` dans `signaux` permet de stocker des structures variees par source
- Les index et contraintes CHECK garantissent l'integrite des scores
- La comparaison temporelle necessite des requetes performantes sur les dates

### 2. SQLAlchemy async (pas d'ORM sync)
- FastAPI est async par nature, SQLAlchemy async evite le blocking
- `asyncpg` est le driver PostgreSQL le plus performant en Python

### 3. HTMX au lieu d'un framework JS
- Pas besoin de SPA pour un outil solo
- HTMX envoie des requetes et remplace des fragments HTML — simple et efficace
- Le serveur garde le controle du rendu (templates Jinja2)

### 4. Tailwind via CDN (pas de build step)
- En Phase 1, le CDN suffit et evite un pipeline Node.js
- Migration vers build Tailwind possible en Phase 3 si necessaire

### 5. Architecture sources extensible
- Chaque source est un module dans `services/sources/`
- Interface commune : `async def fetch_xxx(mot_cle) -> {"donnees": dict, "score_partiel": int}`
- Ajouter Reddit ou Product Hunt = ajouter un fichier dans `sources/`

### 6. Scoring pondere
- Phase 1 : seul `score_demande` (Google Trends) est reel, les autres sont neutres (50)
- La ponderation est : demande 40%, douleur 20%, concurrence 20%, monetisation 20%
- En Phase 2, chaque source alimentera son score propre

## Schema base de donnees

3 tables : `niches` → `analyses` → `signaux`

- Une niche = un mot-cle unique
- Une analyse = un scoring a un instant T (permet la comparaison temporelle)
- Un signal = donnees brutes d'une source pour une analyse donnee

## Flux de donnees

```
Utilisateur saisit mot-cle
    → POST /analyser
        → Cherche/cree la niche en base
        → Appelle les sources (google_trends en V1)
        → Calcule les scores (scoring.py)
        → Sauvegarde analyse + signaux en base
        → Retourne le fragment HTML fiche.html via HTMX
```
