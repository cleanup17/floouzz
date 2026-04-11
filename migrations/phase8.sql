-- Floouzz Phase 8 — Affiliate Finder
-- A appliquer apres phase7.sql
-- Ajoute le champ affiliate_finder JSONB a la table analyses pour stocker
-- le resultat de l'analyse d'ecosysteme d'affiliation du mode Analyse.

-- Champ JSONB pour le retour complet de affiliate_finder.chercher_affiliation() :
--   score_affiliation, verdict (AUCUN/FAIBLE/BON/EXCELLENT), verdict_raison,
--   plateformes_detectees, programmes (liste detaillee avec commission/cookie),
--   opportunites, requetes_utilisees, nb_resultats_analyses.
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS affiliate_finder JSONB;
