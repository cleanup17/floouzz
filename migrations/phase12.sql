-- Floouzz Phase 12 — Score Potentiel International
-- A appliquer apres phase10.sql
-- Ajoute le champ international JSONB a la table analyses pour stocker
-- le resultat de l'analyse multi-marches (USA, Allemagne, Espagne, Italie).

ALTER TABLE analyses ADD COLUMN IF NOT EXISTS international JSONB;
