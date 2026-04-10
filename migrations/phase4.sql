-- Floouzz Phase 4 — brancher pipeline_ia sur le mode Analyse
-- A appliquer apres phase3.sql
-- Projet neuf : pas de retro-compatibilite, on remplace proprement.

-- ===========================================================================
-- Refonte de la table analyses pour le format pipeline_ia
-- ===========================================================================
-- L'ancien scoring (0-100, verdict explorer/watchlist/abandonner, opportunite
-- narrative) est remplace par le format pipeline_ia : scores 0-10 avec
-- justifications, verdict GO/WATCH/SKIP, resume_fr, mots-cles SEO, risque YMYL.

-- Supprimer les anciennes contraintes (scores 0-100, verdict legacy)
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_global_check;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_demande_check;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_douleur_check;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_concurrence_check;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_monetisation_check;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_verdict_check;

-- Supprimer la colonne opportunite (remplacee par resume_fr + verdict_raison)
ALTER TABLE analyses DROP COLUMN IF EXISTS opportunite;

-- Nouveaux champs pipeline_ia (idempotent pour permettre un rejeu partiel)
ALTER TABLE analyses
    ADD COLUMN IF NOT EXISTS pipeline_ia     JSONB,
    ADD COLUMN IF NOT EXISTS resume_fr       TEXT,
    ADD COLUMN IF NOT EXISTS verdict_raison  TEXT,
    ADD COLUMN IF NOT EXISTS mots_cles_seo   VARCHAR(100)[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS tags            VARCHAR(50)[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS risque_ymyl     BOOLEAN        NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS niche_detectee  VARCHAR(255);

-- Nouvelles contraintes : scores 0-10, verdict GO/WATCH/SKIP
-- On drop avant add pour etre idempotent sur les rejeux
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_global_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_score_global_check
    CHECK (score_global BETWEEN 0 AND 10);

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_demande_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_score_demande_check
    CHECK (score_demande BETWEEN 0 AND 10);

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_douleur_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_score_douleur_check
    CHECK (score_douleur BETWEEN 0 AND 10);

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_concurrence_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_score_concurrence_check
    CHECK (score_concurrence BETWEEN 0 AND 10);

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_score_monetisation_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_score_monetisation_check
    CHECK (score_monetisation BETWEEN 0 AND 10);

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_verdict_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_verdict_check
    CHECK (verdict IN ('GO', 'WATCH', 'SKIP'));

-- Index utile pour la recherche par niche_detectee (deduplication cote Analyse)
CREATE INDEX IF NOT EXISTS idx_analyses_niche_detectee
    ON analyses (LOWER(niche_detectee))
    WHERE niche_detectee IS NOT NULL;

-- Index pour filtrer par verdict en UI
CREATE INDEX IF NOT EXISTS idx_analyses_verdict ON analyses (verdict);
