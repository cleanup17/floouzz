-- Floouzz Phase 13 — Amazon Market
-- A appliquer apres phase12.sql
-- Ajoute le champ amazon_market JSONB a la table analyses pour stocker
-- le resultat de l'analyse de volume produits Amazon FR.

ALTER TABLE analyses ADD COLUMN IF NOT EXISTS amazon_market JSONB;
