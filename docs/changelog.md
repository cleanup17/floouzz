# Floouzz — Changelog

## [0.2.0] — 2026-04-07

### Phase 2 — Mode Decouverte + Multi-sources

**Ajoute :**
- Mode Decouverte : dashboard signaux quotidiens avec filtres thematiques
- Sources SerpAPI : Google Trends, Jobs, Search (CPC/PAA), News
- Sources Apify : Reddit, Product Hunt, Hacker News, URL scraper generique
- Endpoint webhook securise pour reception signaux n8n
- Service enrichissement Claude API (resume, pertinence, tags)
- Service traduction DeepL FR→EN
- Scanner quotidien (collecte + enrichissement decouple)
- Admin sources configurable (CRUD, test, activation)
- Page parametres (cles API masquees, thematiques, stats scan)
- Scoring multi-sources 4 dimensions (demande, douleur, concurrence, monetisation)
- Seed 6 sources par defaut au demarrage
- Navigation 3 pages (Decouverte, Analyser, Parametres)
- Schemas Pydantic Phase 2 (Source, Decouverte, Thematique, WebhookSignal)
- Migration SQL Phase 2 (tables sources, decouvertes, thematiques, preferences)

**Modifie :**
- Google Trends passe de pytrends a SerpAPI (plus stable)
- Page d'accueil redirige vers le mode Decouverte
- Scoring enrichi avec moyenne par dimension et opportunite narrative
- Config : cles API optionnelles dans .env

---

## [0.1.0] — 2026-04-06

### Phase 1 — MVP

**Ajoute :**
- Structure projet Docker Compose (FastAPI + PostgreSQL 16)
- Schema base de donnees : tables `niches`, `analyses`, `signaux`
- Modeles SQLAlchemy async avec relations et contraintes
- Source Google Trends via pytrends (tendances FR, 12 mois)
- Service de scoring avec ponderation et verdict automatique
- Interface HTMX + Tailwind CSS : saisie mot-cle, fiche niche, historique
- Fragments HTMX pour mise a jour dynamique sans rechargement
- Page historique par niche avec toutes les analyses precedentes
- Documentation architecture technique
