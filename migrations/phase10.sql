-- Floouzz Phase 10 — Marketplace Gap Detector
-- A appliquer apres phase9.sql
-- Ajoute le champ marketplace_gap JSONB a la table analyses pour stocker
-- le resultat de l'analyse de presence sur les marketplaces FR.

ALTER TABLE analyses ADD COLUMN IF NOT EXISTS marketplace_gap JSONB;
