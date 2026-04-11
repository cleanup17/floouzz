-- Floouzz Phase 7 — SERP Gap Detector
-- A appliquer apres phase6.sql
-- Ajoute le champ serp_gap JSONB a la table analyses pour stocker le
-- resultat de l'analyse concurrentielle SEO du mode Analyse.

-- Champ JSONB pour le retour complet de serp_gap.analyser_serp() :
--   score_difficulte, verdict, verdict_raison, opportunites,
--   faiblesses_detectees, top_10 (snapshot structure du top 10 Google).
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS serp_gap JSONB;
