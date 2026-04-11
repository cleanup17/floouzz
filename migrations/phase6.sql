-- Floouzz Phase 6 — nouvelle source Sitemap Intelligence
-- A appliquer apres phase5.sql
-- Ajoute 'sitemap' aux types de source acceptes par la contrainte CHECK.

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_type_check;
ALTER TABLE sources ADD CONSTRAINT sources_type_check
    CHECK (type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook', 'sitemap'));
