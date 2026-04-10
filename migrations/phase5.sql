-- Floouzz Phase 5 — brancher pipeline_ia sur le mode Decouverte
-- A appliquer apres phase4.sql
-- Projet neuf : pas de retro-compatibilite, on remplace proprement.

-- ===========================================================================
-- Refonte de la table decouvertes pour le format pipeline_ia
-- ===========================================================================
-- L'ancien enrichissement (score_pertinence 0-100, resume texte libre) est
-- remplace par le format pipeline_ia : 4 scores 0-10 avec justifications,
-- verdict GO/WATCH/SKIP, resume_fr, mots-cles SEO, risque YMYL, JSONB brut.

-- Supprimer les anciennes contraintes et colonnes legacy
ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_pertinence_check;
ALTER TABLE decouvertes DROP COLUMN IF EXISTS score_pertinence;
ALTER TABLE decouvertes DROP COLUMN IF EXISTS resume;

-- Nouveaux champs pipeline_ia (idempotent)
ALTER TABLE decouvertes
    ADD COLUMN IF NOT EXISTS pipeline_ia         JSONB,
    ADD COLUMN IF NOT EXISTS resume_fr           TEXT,
    ADD COLUMN IF NOT EXISTS verdict             VARCHAR(20),
    ADD COLUMN IF NOT EXISTS verdict_raison      TEXT,
    ADD COLUMN IF NOT EXISTS score_global        INTEGER,
    ADD COLUMN IF NOT EXISTS score_demande       INTEGER,
    ADD COLUMN IF NOT EXISTS score_douleur       INTEGER,
    ADD COLUMN IF NOT EXISTS score_concurrence   INTEGER,
    ADD COLUMN IF NOT EXISTS score_monetisation  INTEGER,
    ADD COLUMN IF NOT EXISTS mots_cles_seo       VARCHAR(100)[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS risque_ymyl         BOOLEAN        NOT NULL DEFAULT FALSE;

-- Contraintes : scores 0-10, verdict strict GO/WATCH/SKIP (idempotent)
ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_global_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_score_global_check
    CHECK (score_global BETWEEN 0 AND 10);

ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_demande_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_score_demande_check
    CHECK (score_demande BETWEEN 0 AND 10);

ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_douleur_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_score_douleur_check
    CHECK (score_douleur BETWEEN 0 AND 10);

ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_concurrence_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_score_concurrence_check
    CHECK (score_concurrence BETWEEN 0 AND 10);

ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_score_monetisation_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_score_monetisation_check
    CHECK (score_monetisation BETWEEN 0 AND 10);

ALTER TABLE decouvertes DROP CONSTRAINT IF EXISTS decouvertes_verdict_check;
ALTER TABLE decouvertes ADD CONSTRAINT decouvertes_verdict_check
    CHECK (verdict IN ('GO', 'WATCH', 'SKIP'));

-- Index utiles pour les filtres UI (verdict + score_global desc)
CREATE INDEX IF NOT EXISTS idx_decouvertes_verdict ON decouvertes (verdict);
CREATE INDEX IF NOT EXISTS idx_decouvertes_score_global
    ON decouvertes (score_global DESC NULLS LAST);
