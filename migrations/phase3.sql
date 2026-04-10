-- Floouzz Phase 3 — pipeline IA unifie + logs scanner
-- A appliquer apres phase2.sql

-- ===========================================================================
-- ALTER decouvertes : colonne niche_detectee pour la deduplication
-- ===========================================================================
-- Stocke la niche detectee par le pipeline IA. Permet une deduplication
-- par match exact (critere 1) directement en SQL.
ALTER TABLE decouvertes ADD COLUMN IF NOT EXISTS niche_detectee VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_decouvertes_niche_detectee
    ON decouvertes (LOWER(niche_detectee))
    WHERE niche_detectee IS NOT NULL;


-- ===========================================================================
-- Table cache_ia — cache des appels au pipeline IA (TTL 24h)
-- ===========================================================================
-- Evite les appels doublons a Claude sur un meme contenu.
-- La cle est un hash SHA-256 du contenu normalise (titre + contenu + thematiques).
-- La colonne source permet de purger le cache par source (ex: reddit, google_trends).
CREATE TABLE IF NOT EXISTS cache_ia (
    hash_contenu VARCHAR(64) PRIMARY KEY,
    source VARCHAR(50),
    resultat JSONB NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cache_ia_expires_at ON cache_ia (expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_ia_source ON cache_ia (source);

-- ===========================================================================
-- Table executions_scanner — logs de chaque execution du scanner
-- ===========================================================================
-- Trace chaque passage d'une source : succes/echec, nb de signaux, duree.
-- Permet de monitorer la sante du scanner et d'afficher l'historique en UI.
CREATE TABLE IF NOT EXISTS executions_scanner (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source VARCHAR(50) NOT NULL,
    statut VARCHAR(20) NOT NULL CHECK (statut IN ('succes', 'echec', 'partiel')),
    nb_signaux INTEGER NOT NULL DEFAULT 0,
    erreur TEXT,
    duree_ms INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_executions_scanner_source ON executions_scanner (source);
CREATE INDEX IF NOT EXISTS idx_executions_scanner_created_at ON executions_scanner (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_executions_scanner_statut ON executions_scanner (statut);
