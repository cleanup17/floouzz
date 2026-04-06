# Floouzz Phase 2 — Mode Decouverte + Multi-sources

**Date** : 2026-04-06
**Statut** : Valide — pret pour implementation
**Autrice** : Nathalie Millasseau (LICENCE WEB)

---

## 1. Vision

Floouzz passe d'un outil de scoring a la demande a un **radar de niches** qui remonte des signaux chauds automatiquement chaque matin. L'utilisatrice ouvre Floouzz, voit ce qui bouge, et approfondit ce qui l'interesse.

### Deux modes complementaires

| Mode | Declencheur | Resultat |
|------|------------|----------|
| **Decouverte** (nouveau) | Scan automatique quotidien 6h + rafraichissement manuel | Dashboard de signaux filtres, resumes, tagges |
| **Analyse** (existant) | Saisie manuelle ou clic "Approfondir" | Fiche niche scoree complete avec verdict |

### Flow utilisateur

```
[6h00] Scan automatique → collecte donnees brutes
[6h15] Enrichissement Claude → filtrage, resume, tags
[Matin] L'utilisatrice ouvre Floouzz
    → Dashboard signaux du jour (filtrables par theme)
    → Clic "Approfondir" sur un signal interessant
    → Analyse complete (scoring 4 dimensions)
    → Fiche niche + historique
    → Clic "Pas pertinent" sur les signaux non interessants (feedback)
```

---

## 2. Architecture technique

### Pipeline decouple (robuste)

**Etape 1 — Collecte (6h00)**
- Le scanner lit la table `sources` (actives avec cron valide)
- Pour chaque source : appel SerpAPI / Apify / webhook selon le type
- Stockage des donnees brutes en table `decouvertes`
- Si une source echoue, les autres continuent

**Etape 2 — Enrichissement (6h15)**
- Claude API lit les decouvertes brutes du jour
- Pour chaque signal :
  - Score de pertinence 0-100
  - Resume en 1-2 phrases
  - Tags thematiques (1-3 parmi la liste configurable)
- Les signaux < 30 de pertinence sont marques mais pas affiches par defaut
- Claude prend en compte les preferences (signaux ignores/approfondis precedemment)

**Etape 3 — Consultation (a la demande)**
- Dashboard avec signaux enrichis du jour
- Filtres par theme
- Actions : Approfondir / Pas pertinent

**Etape 4 — Rafraichir (manuel)**
- Bouton qui relance collecte + enrichissement
- Meme pipeline, execution immediate

### Sources de donnees

#### SerpAPI (tout ce qui est Google)

| Endpoint | Signal | Score alimente |
|----------|--------|---------------|
| Google Trends | Tendances de recherche FR | Demande |
| Google Search | CPC, nombre de resultats, ads | Monetisation, Concurrence |
| Google Jobs | Offres d'emploi | Monetisation |
| People Also Ask | Questions posees | Douleur |
| Google News | Actualites | Demande (timing) |

#### Apify (communautes)

| Actor | Signal | Score alimente |
|-------|--------|---------------|
| Reddit scraper | Discussions, plaintes, volume | Douleur |
| Product Hunt scraper | Produits existants, votes | Concurrence |
| Hacker News scraper | Tendances tech US | Douleur, Demande |
| URL scraper (generique) | Blogs, flux RSS, pages web | Variable |

#### Webhooks (n8n / sources complexes)

- Endpoint `POST /api/webhooks/signal` securise par token
- n8n envoie des signaux au format standardise
- Cas d'usage : LinkedIn, Google Sheets, chaines multi-etapes

#### Traduction (DeepL API Free)

- Chaque mot-cle francais est traduit en anglais automatiquement
- La traduction est stockee dans la table `niches` (champ `mot_cle_en`)
- Les sources US sont interrogees avec le mot-cle anglais
- 500 000 caracteres/mois gratuits — largement suffisant

### Scoring Phase 2

| Score | Poids | Sources |
|-------|-------|---------|
| Demande | 40% | Google Trends + Google News + Google Suggest |
| Douleur | 20% | Reddit + Hacker News + People Also Ask |
| Concurrence | 20% | Product Hunt + Google Search (nb resultats, ads) |
| Monetisation | 20% | Google Jobs (nb offres) + Google Search (CPC) |

Verdict inchange :
- Score > 70 = a explorer
- Score 50-70 = watchlist
- Score < 50 = abandonner

---

## 3. Schema base de donnees

### Tables existantes (inchangees)

```sql
niches (id, mot_cle, created_at)
analyses (id, niche_id, scores..., opportunite, verdict, created_at)
signaux (id, analyse_id, niche_id, source, donnees, score_partiel, created_at)
```

### Modification table niches

```sql
ALTER TABLE niches ADD COLUMN mot_cle_en VARCHAR(255);
-- Traduction anglaise du mot-cle (DeepL), stockee pour ne pas re-traduire
```

### Nouvelles tables

```sql
-- Sources configurables (admin)
sources (
    id              UUID PRIMARY KEY,
    nom             VARCHAR(100) NOT NULL,
    type            VARCHAR(20) NOT NULL,
        -- 'serpapi' / 'apify_actor' / 'apify_url' / 'api' / 'webhook'
    config          JSONB NOT NULL DEFAULT '{}',
        -- parametres specifiques au type de source
        -- serpapi : {"engine": "google_trends", "gl": "fr", "hl": "fr"}
        -- apify_actor : {"actor_id": "trudax/reddit-scraper", "input": {...}}
        -- apify_url : {"urls": ["https://..."], "selector": "..."}
        -- api : {"url": "https://...", "method": "GET", "headers": {...}}
        -- webhook : {"token": "flz_xxxx"} (token de securite)
    cle_api_ref     VARCHAR(50),
        -- nom de la variable d'environnement : "SERPAPI_KEY", "APIFY_TOKEN", null
    actif           BOOLEAN DEFAULT true,
    cron_expr       VARCHAR(50) DEFAULT '0 6 * * *',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Decouvertes (signaux bruts du scan quotidien)
decouvertes (
    id                  UUID PRIMARY KEY,
    source_id           UUID NOT NULL REFERENCES sources(id),
    titre               VARCHAR(500) NOT NULL,
    url                 TEXT,
    donnees             JSONB NOT NULL DEFAULT '{}',
    score_pertinence    INTEGER CHECK (score_pertinence BETWEEN 0 AND 100),
    resume              TEXT,
    tags                VARCHAR(50)[] DEFAULT '{}',
    statut              VARCHAR(20) DEFAULT 'nouveau'
        CHECK (statut IN ('nouveau', 'vu', 'approfondi', 'ignore')),
    mot_cle_suggere     VARCHAR(255),
    niche_id            UUID REFERENCES niches(id),
    scan_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Thematiques (filtres configurables)
thematiques (
    id          UUID PRIMARY KEY,
    nom         VARCHAR(100) UNIQUE NOT NULL,
    actif       BOOLEAN DEFAULT true,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Preferences utilisateur (feedback pour affiner)
preferences (
    id              UUID PRIMARY KEY,
    type            VARCHAR(20) NOT NULL CHECK (type IN ('like', 'ignore')),
    decouverte_id   UUID NOT NULL REFERENCES decouvertes(id),
    tags_associes   VARCHAR(50)[] DEFAULT '{}',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Index

```sql
CREATE INDEX idx_decouvertes_scan_date ON decouvertes(scan_date DESC);
CREATE INDEX idx_decouvertes_statut ON decouvertes(statut);
CREATE INDEX idx_decouvertes_tags ON decouvertes USING GIN(tags);
CREATE INDEX idx_decouvertes_source_id ON decouvertes(source_id);
CREATE INDEX idx_preferences_type ON preferences(type);
CREATE INDEX idx_sources_actif ON sources(actif);
```

---

## 4. Interface

### Navigation

3 pages principales :
- **Decouverte** (page d'accueil) — signaux du jour
- **Analyser** — recherche manuelle + fiche niche (existant)
- **Parametres** — admin sources, cles API, thematiques

### Page Decouverte

**En-tete :** date du jour + bouton "Rafraichir"

**Filtres thematiques :** barre de tags cliquables [Tous] [IA] [E-commerce] [Video] [+]
- Le compteur de signaux par tag est affiche
- Le [+] ouvre une modale pour ajouter un theme

**Liste de signaux :** cartes empilees, triees par score de pertinence decroissant
Chaque carte contient :
- Score de pertinence (badge couleur : vert > 70, orange 50-70, rouge < 50)
- Titre du signal (mot-cle detecte)
- Sources ayant remonte le signal + chiffres bruts
- Resume Claude (1-2 phrases)
- Tags thematiques (badges cliquables)
- Bouton "Approfondir" → cree la niche + lance l'analyse complete
- Bouton "Pas pertinent" → marque comme ignore + stocke le feedback

**Pied de page :** stats semaine (nb signaux, nb approfondis, nb niches)

**Zone "Signaux ignores" :** repliee par defaut, deroulable

### Page Analyser (amelioree)

L'existant + :
- Pre-remplissage du mot-cle quand on vient de "Approfondir"
- L'analyse utilise toutes les sources actives (plus seulement Google Trends)
- La fiche affiche les 4 scores avec donnees reelles
- Liste des niches recentes en dessous (existant)

### Page Parametres

**Section Cles API :**
- Lecture seule — affiche le nom de la variable .env, les 4 premiers et 4 derniers caracteres de la cle, le statut (valide/invalide) et les credits restants si disponible
- Bouton "Tester" par cle pour verifier la validite
- Les cles ne sont jamais affichees en entier ni stockees en base

**Section Sources :**
- Liste des sources avec nom, type, cle_api_ref, statut actif/inactif, cron
- Bouton [+ Ajouter] → modale de creation
- Bouton [Modifier] par source → modale d'edition
- Bouton [Tester] par source → lance un appel reel et affiche le resultat brut
- Types supportes : SerpAPI, Apify Actor, Apify URL, API directe, Webhook

**Section Thematiques :**
- Liste de badges editables
- Ajout / suppression de thematiques
- Les thematiques sont utilisees par Claude pour tagger les signaux

**Section Scan :**
- Heure du cron configurable
- Date/heure du dernier scan
- Stats du dernier scan (nb collectes, nb apres filtrage)

---

## 5. Cles API necessaires

```env
SERPAPI_KEY=          # Google: Trends, Jobs, Search, News, PAA
APIFY_TOKEN=          # Communautes: Reddit, Product Hunt, HN, URLs
DEEPL_API_KEY=        # Traduction FR→EN des mots-cles
ANTHROPIC_API_KEY=    # Resume, pertinence, tagging des signaux
```

Toutes sur plan gratuit ou quasi pour un usage solo quotidien.
Les cles restent exclusivement dans le fichier `.env` — jamais en base.

---

## 6. Structure fichiers cible

```
app/
├── main.py
├── config.py
├── database.py
├── models.py                    # + Source, Decouverte, Thematique, Preference
├── schemas.py                   # + schemas correspondants
├── routers/
│   ├── niches.py                # existant (mode Analyse)
│   ├── decouvertes.py           # nouveau (mode Decouverte)
│   ├── sources.py               # nouveau (admin sources)
│   ├── parametres.py            # nouveau (parametres, cles API)
│   └── webhooks.py              # nouveau (reception signaux n8n)
├── services/
│   ├── scanner.py               # nouveau — orchestrateur du scan quotidien
│   ├── enrichissement.py        # nouveau — Claude API : resume, pertinence, tags
│   ├── scoring.py               # existant — enrichi avec nouvelles sources
│   ├── traduction.py            # nouveau — DeepL FR→EN
│   └── sources/
│       ├── base.py              # nouveau — interface commune des sources
│       ├── google_trends.py     # refactorise — SerpAPI au lieu de pytrends
│       ├── google_jobs.py       # nouveau
│       ├── google_search.py     # nouveau (SERP, CPC, PAA)
│       ├── google_news.py       # nouveau
│       ├── reddit.py            # nouveau — Apify
│       ├── producthunt.py       # nouveau — Apify
│       ├── hackernews.py        # nouveau — Apify ou API Algolia
│       ├── apify_url.py         # nouveau — scraper URL generique
│       └── webhook.py           # nouveau — reception n8n
└── templates/
    ├── base.html                # modifie — ajout navigation 3 pages
    ├── decouverte.html          # nouveau — dashboard signaux
    ├── index.html               # existant (mode Analyse)
    ├── historique.html           # existant
    ├── parametres.html          # nouveau
    └── partials/
        ├── fiche.html           # existant
        ├── signal_carte.html    # nouveau — carte signal decouverte
        ├── source_modale.html   # nouveau — modale edition source
        └── erreur.html          # existant
```

---

## 7. Contraintes et regles

### Securite
- Cles API dans `.env` uniquement — jamais en base, jamais loguees
- Tokens webhook valides par comparaison avec variable d'environnement
- Validation Pydantic sur tous les endpoints
- Requetes preparees SQLAlchemy — jamais de f-strings SQL
- Echappement Jinja2 systematique

### Performance
- Scan quotidien < 5 minutes (sources en parallele)
- Enrichissement Claude < 2 minutes (batch de signaux)
- Dashboard < 1 seconde (requete indexee sur scan_date)
- Analyse approfondie < 60 secondes (objectif existant)

### Couts
- Plan gratuit SerpAPI : 100 recherches/mois → ~3 requetes/jour (Trends + Jobs + Search)
- Plan gratuit Apify : 5$/mois credits → ~1 scan/jour (Reddit + PH + HN)
- Plan gratuit DeepL : 500k car/mois → largement suffisant
- Claude API : ~0.05$/jour pour ~20 signaux resumes (Haiku)
- Total estime : < 2$/mois

### Deploiement
- Dev : Docker Desktop local (Windows, port 8001)
- Prod : Synology DS218+ (Docker, cron systemd ou tache planifiee Synology)
- Meme docker-compose.yml partout
