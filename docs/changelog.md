# Floouzz — Changelog

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
