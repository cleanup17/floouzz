-- Floouzz — Schéma initial V1
-- Extension UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Table des niches (un mot-clé ou secteur)
CREATE TABLE IF NOT EXISTS niches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    mot_cle VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Table des analyses (une session de scoring à un instant T)
CREATE TABLE IF NOT EXISTS analyses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    score_global INTEGER CHECK (score_global BETWEEN 0 AND 100),
    score_demande INTEGER CHECK (score_demande BETWEEN 0 AND 100),
    score_douleur INTEGER CHECK (score_douleur BETWEEN 0 AND 100),
    score_concurrence INTEGER CHECK (score_concurrence BETWEEN 0 AND 100),
    score_monetisation INTEGER CHECK (score_monetisation BETWEEN 0 AND 100),
    opportunite TEXT,
    verdict VARCHAR(20) CHECK (verdict IN ('explorer', 'watchlist', 'abandonner')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Table des signaux (données brutes d'une source)
CREATE TABLE IF NOT EXISTS signaux (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    analyse_id UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    source VARCHAR(50) NOT NULL,
    donnees JSONB NOT NULL DEFAULT '{}',
    score_partiel INTEGER CHECK (score_partiel BETWEEN 0 AND 100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_analyses_niche_id ON analyses(niche_id);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signaux_analyse_id ON signaux(analyse_id);
CREATE INDEX IF NOT EXISTS idx_signaux_niche_id ON signaux(niche_id);
CREATE INDEX IF NOT EXISTS idx_signaux_source ON signaux(source);
