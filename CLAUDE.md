# CLAUDE.md — Projet Floouzz

## Contexte projet
**Produit** : Floouzz — Outil de recherche et veille de niches de marché
**Autrice** : Nathalie Millasseau — LICENCE WEB, consultante IA/Web
**État** : Phase 1 MVP livrée, Phase 2 (Découverte + multi-sources) en conception
**Utilisatrice** : Solo — consultante numérique, cherche des niches pour SaaS / offres IA

## Architecture technique

### Stack
- **Backend** : Python 3.13 + FastAPI
- **Base de données** : PostgreSQL 16
- **Conteneurs** : Docker + Docker Compose (app, db)
- **Frontend** : HTMX + Tailwind CSS (CDN) + Jinja2
- **Sources de données** : SerpAPI (Google), Apify (Reddit, PH, HN), webhooks (n8n)
- **IA** : Claude API (résumés, filtrage pertinence, tagging)
- **Traduction** : DeepL API Free (FR→EN)

### Infrastructure
- **Dev** : Windows, Laragon, Docker Desktop (port 8001)
- **Prod future** : Synology DS218+ ou VPS

## Architecture applicative

### Services
```
app/services/
├── scanner.py              ← orchestrateur du scan quotidien
├── enrichissement.py       ← Claude API : résumé, pertinence, tags
├── scoring.py              ← calcul score global + verdict
├── traduction.py           ← DeepL FR→EN
└── sources/
    ├── google_trends.py    ← SerpAPI Google Trends
    ├── google_jobs.py      ← SerpAPI Google Jobs
    ├── google_paa.py       ← SerpAPI People Also Ask
    ├── reddit.py           ← Apify Reddit scraper
    ├── producthunt.py      ← Apify Product Hunt
    ├── hackernews.py       ← Apify / API Algolia
    └── webhook.py          ← réception signaux n8n
```

### Modules
```
app/routers/
├── niches.py       ← analyse de niche (mode Analyse)
├── decouvertes.py  ← dashboard signaux (mode Découverte)
├── sources.py      ← admin des sources
└── parametres.py   ← paramètres, état des clés API
```

**Schéma BDD** : @docs/architecture.md

## Règles de développement

### Sécurité (NON NÉGOCIABLE)
- Ne jamais logger clés API, tokens, ou données personnelles
- `.env` : jamais lu, modifié ou commité par Claude
- `SERPAPI_KEY`, `APIFY_TOKEN`, `DEEPL_API_KEY`, `ANTHROPIC_API_KEY` → variables d'environnement uniquement
- Clés API JAMAIS stockées en base de données
- Validation Pydantic OBLIGATOIRE sur tous les endpoints FastAPI
- ALWAYS valider toutes les entrées utilisateur avec Pydantic
- ALWAYS utiliser les requêtes préparées SQLAlchemy (jamais de f-strings SQL)
- ALWAYS échapper les sorties Jinja2 via {{ variable }} — jamais {{ variable | safe }} sans justification
- ALWAYS vérifier les tokens webhook avant d'accepter des données
- NEVER construire des requêtes SQL par concaténation ou f-string
- NEVER faire confiance aux données entrantes sans validation préalable
- NEVER stocker de secrets en base de données
- NEVER commiter des clés, tokens, ou mots de passe

### Code Python
- Python 3.13+, type hints obligatoires sur toutes les fonctions
- Docstrings en français pour fonctions métier
- Commenter le POURQUOI, pas le QUOI
- Commentaires en français sur la logique métier complexe
- Tests pytest obligatoires pour tout nouvel endpoint
- Noms de variables et fonctions en anglais

### Git
- Branches : `main` (prod), `feature/`, `fix/`, `refactor/`
- Commits : en français, préfixés (`feat:`, `fix:`, `refactor:`, `docs:`)
- NEVER committer sur `main` directement
- NEVER push sans accord explicite

### Sources de données
- Chaque source = un fichier dans `app/services/sources/`
- Interface commune : `async def fetch(mot_cle: str, config: dict) -> dict`
- Les sources sont configurables via l'interface admin (table `sources`)
- La référence `cle_api_ref` pointe vers le NOM de la variable d'environnement, jamais la valeur

## Ce que tu NE FAIS PAS
- Modifier `.env`, `.env.*`, `secrets/`, `config/credentials*`
- Stocker des clés API, tokens ou secrets en base de données
- Commiter des clés, tokens, ou mots de passe
- Utiliser `| safe` dans Jinja2 sans commentaire explicatif
- Construire des requêtes SQL par concaténation
- Ajouter des dépendances sans raison explicite
- Refactorer du code non demandé
- Supprimer ou réécrire un fichier entier sans accord

## Références
- Architecture : @docs/architecture.md
- Changelog : @docs/changelog.md
- Spec Phase 2 : @docs/superpowers/specs/ (à venir)
