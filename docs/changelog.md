# Floouzz — Changelog

> Tickets de dette technique : voir [dette-technique.md](dette-technique.md)

## [0.5.0] — 2026-04-11

### SERP Gap Detector — analyse concurrentielle SEO Claude-powered

**Ajoute :**
- Service `app/services/serp_gap.py` : pour un mot-cle, recupere le top 10
  Google.fr via SerpAPI (`num=20` tronque a 10) puis envoie les resultats a
  Claude Sonnet 4.5 avec un prompt expert SEO. Retourne un dict structure :
  `score_difficulte` 0-10, `verdict` FACILE/MOYEN/DIFFICILE, `verdict_raison`,
  `opportunites` (2-5 pistes actionnables), `faiblesses_detectees` (0-5),
  `top_10` (snapshot structure).
- Cache 7 jours dans `cache_ia` avec `source='serp_gap'` (preserve le quota
  SerpAPI et evite les appels Claude doublons).
- Detection heuristique du type de page (landing/blog/shop/forum/annuaire/
  wikipedia/pollution) comme contexte injecte dans le prompt Claude.
- Route `POST /analyser` : appel parallele `asyncio.gather(pipeline_ia,
  serp_gap)` pour reduire la latence totale.
- Nouveau champ `analyses.serp_gap JSONB` (migration `phase7.sql`).
- Bloc "Concurrence SEO" dans `partials/fiche.html` : badge verdict colore
  (FACILE=vert, MOYEN=orange, DIFFICILE=rouge), raison, opportunites avec
  `+`, faiblesses avec `-`, tableau top 10 repliable via `<details>`.
- Section "Concurrence SEO" dans l'export Markdown avec tableau du top 10
  et echappement des pipes dans les titres.
- Nouvelle route `GET /analyse/{id}` : affiche le detail d'une analyse
  passee sans en relancer une nouvelle (reuse de `partials/fiche.html`).
- Lignes cliquables dans `historique.html` : clic sur une ligne -> navigation
  vers `/analyse/{id}`, lien `.md` preserve via `event.stopPropagation()`.
- Script dry-run `scripts/test_serp_gap.py` pour iterer sur le prompt sans
  toucher au module ni a la BDD.
- Fixture `mock_serp_gap` dans `tests/conftest.py` : evite les appels reels
  SerpAPI/Claude pendant les tests du router niches (fix bug critique
  decouvert pendant l'integration).

**Valide :** 4 tests du prompt sur vrais mots-cles (coquillage allaitement,
veilleuse coranique, lave vitres magnetique, assurance auto pas cher) — les
3 premiers retournent FACILE 2-3/10, le dernier DIFFICILE 9/10. Prompt
discriminant et actionnable.

**Tests :** 108 PASS.

---

## [0.4.1] — 2026-04-11

### Source Sitemap Intelligence + edition de config dans l'UI admin

**Ajoute :**
- Connecteur `app/services/sources/sitemap.py` : crawl les sitemaps XML
  de sites concurrents pour detecter les pages publiees recemment.
  Supporte sitemap classique + sitemap index recursif + gzip. Score
  fraicheur base sur `<lastmod>` (aujourd'hui=90, <3j=80, <30j=40).
  Config : liste de sitemaps, `max_urls_par_sitemap`, `max_age_days`,
  `max_resultats`, `max_index_depth`.
- Source "Sitemap Intelligence" inseree automatiquement via la nouvelle
  fonction idempotente `seed_sources_manquantes()` appelee dans le
  `lifespan`. Active `False` par defaut, URLs placeholder
  `REMPLACER-PAR-CONCURRENT-*` a editer avant activation.
- Migration `phase6.sql` : ajoute `'sitemap'` a la contrainte CHECK
  `sources_type_check`.
- Route `PUT /api/sources/{id}` : edition de la config JSONB d'une
  source existante. Parsing JSON robuste (rejette si invalide plutot
  que d'ecraser avec `{}`).
- Modale generique create/edit dans `/parametres/` avec pre-remplissage
  JSON formate, option `sitemap` dans le select, champ cron editable.
- Script dry-run `scripts/test_sitemap_scan.py` pour tester le
  connecteur sitemap en isolation (aucun appel Claude, aucun INSERT).

**Corrige :**
- **Bug critique soumission form modale** : HTMX mettait en cache
  `hx-post` au chargement initial et ignorait les `setAttribute('hx-put')`
  dynamiques. Le form d'edition etait silencieusement casse. Fix :
  retrait des attributs `hx-*`, interception du submit en JS avec
  `fetch()` manuel selon le mode create/edit.
- **Bug rechargement UI apres action source** : les routes retournaient
  un `<script>window.location.href=...</script>` dans le body, jamais
  injecte a cause de `hx-swap='none'`. Fix : helper `_htmx_redirect()`
  qui retourne `204 No Content + HX-Redirect` header, intercepte
  nativement par HTMX.

---

## [0.4.0] — 2026-04-11

### Scheduler automatique + dette Starlette

**Ajoute :**
- Scheduler APScheduler (`AsyncIOScheduler`) branche dans le `lifespan`
  FastAPI : scan quotidien automatique via `run_scan_complet()`
- Parametre `SCAN_CRON` dans `.env` (defaut `"0 6 * * *"` = 6h du matin
  tous les jours, timezone `Europe/Paris`). Vide = scheduler desactive.
- Options robustesse : `max_instances=1` (pas de chevauchement si un scan
  deborde), `coalesce=True` (un seul rattrapage apres downtime)
- 8 tests pytest sur le scheduler (validation cron, timezone, options)
- `logging.basicConfig(level=INFO)` dans `app/main.py` pour que les logs
  applicatifs remontent dans la sortie uvicorn

**Corrige :**
- **Dette technique Starlette `TemplateResponse`** : 8 occurrences
  migrees vers la nouvelle API `TemplateResponse(request, name, context)`
  sur `routers/niches.py` (5), `routers/decouvertes.py` (2),
  `routers/parametres.py` (1). 0 warning de deprecation restant.

**Tests :** 108 PASS (10 niches + 24 traduction + 42 pipeline_ia +
24 deduplication + 8 scheduler).

---

## [0.3.0] — 2026-04-10

### Phase 3 — Pipeline IA unifie + export Markdown

**Ajoute :**
- Service `pipeline_ia.py` : un seul appel Claude produit resume_fr, tags,
  niche_detectee, 4 scores 0-10 avec justifications, score_global, verdict
  GO/WATCH/SKIP, verdict_raison, mots_cles_seo, risque_ymyl
- Cache PostgreSQL 24h (`cache_ia`) avec hash SHA-256 du contenu normalise,
  invalidable par source
- Table `executions_scanner` : log de chaque passage du scanner
  (source, statut, nb_signaux, erreur, duree_ms)
- Service `deduplication.py` : detection des doublons dans les 7 derniers
  jours par niche_detectee exact, tags communs (>=2), ou similarite titre
  (SequenceMatcher > 0.80)
- Route `GET /exports/niche/{niche_id}/markdown` : export fiche niche en
  Markdown telechargeable
- Migration `phase3.sql` : tables `cache_ia`, `executions_scanner`, colonne
  `niche_detectee` sur `decouvertes`
- Migration `phase4.sql` : refonte `analyses` pour le format pipeline_ia

**Modifie :**
- `traduction.py` : role unique EN->FR en amont de pipeline_ia, nouvelle
  fonction batch `traduire_titres()` (un seul appel Claude pour N titres),
  heuristique de detection FR pour eviter les appels inutiles
- Connecteurs sources anglophones (`reddit.py`, `hackernews.py`,
  `producthunt.py`) : pre-traduction EN->FR des titres/taglines via
  `traduire_titres()` avant transmission au scanner
- `scanner.py` : refonte complete de `run_enrichissement()` sur pipeline_ia
  + deduplication + filtre preferences `ignore` + logs executions_scanner
  pour chaque passe de source (succes/partiel/echec, duree_ms, erreur)
- Modele `Decouverte` : scores 0-10 natifs, verdict GO/WATCH/SKIP strict,
  nouveaux champs `resume_fr`, `verdict_raison`, `mots_cles_seo`,
  `risque_ymyl`, `pipeline_ia` (JSONB brut)
- Migration `phase5.sql` : refonte `decouvertes` pour le format pipeline_ia
- `routers/niches.py` : route `/analyser` branchee sur pipeline_ia via
  helper `_construire_synthese()` + chargement des thematiques actives
- Modele `Analyse` : scores 0-10 natifs, verdict GO/WATCH/SKIP strict,
  nouveaux champs `resume_fr`, `verdict_raison`, `mots_cles_seo`, `tags`,
  `risque_ymyl`, `niche_detectee`, `pipeline_ia` (JSONB brut)
- Templates `fiche.html` et `historique.html` : refonte sur le format
  pipeline_ia (scores /10, badges verdict colores, justifications, pills
  cliquables pour SEO/tags, badge YMYL conditionnel)

**Supprime :**
- `services/scoring.py` : remplace par pipeline_ia (mode Analyse)
- `services/enrichissement.py` : remplace par pipeline_ia (mode Decouverte),
  zero consommateur restant
- Colonne `analyses.opportunite` : remplacee par `resume_fr` + `verdict_raison`
- Colonnes `decouvertes.score_pertinence` et `decouvertes.resume` :
  remplacees par les champs pipeline_ia (phase5.sql)

---

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
