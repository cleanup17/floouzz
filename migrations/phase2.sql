-- Floouzz Phase 2 — nouvelles tables + modification niches

-- Ajout traduction anglaise sur niches
ALTER TABLE niches ADD COLUMN IF NOT EXISTS mot_cle_en VARCHAR(255);

-- Sources configurables
CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nom VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook')),
    config JSONB NOT NULL DEFAULT '{}',
    cle_api_ref VARCHAR(50),
    actif BOOLEAN DEFAULT true,
    cron_expr VARCHAR(50) DEFAULT '0 6 * * *',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Decouvertes (signaux du scan quotidien)
CREATE TABLE IF NOT EXISTS decouvertes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    titre VARCHAR(500) NOT NULL,
    url TEXT,
    donnees JSONB NOT NULL DEFAULT '{}',
    score_pertinence INTEGER CHECK (score_pertinence BETWEEN 0 AND 100),
    resume TEXT,
    tags VARCHAR(50)[] DEFAULT '{}',
    statut VARCHAR(20) DEFAULT 'nouveau' CHECK (statut IN ('nouveau', 'vu', 'approfondi', 'ignore')),
    mot_cle_suggere VARCHAR(255),
    niche_id UUID REFERENCES niches(id),
    scan_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Thematiques
CREATE TABLE IF NOT EXISTS thematiques (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nom VARCHAR(100) UNIQUE NOT NULL,
    actif BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Preferences utilisateur (feedback)
CREATE TABLE IF NOT EXISTS preferences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(20) NOT NULL CHECK (type IN ('like', 'ignore')),
    decouverte_id UUID NOT NULL REFERENCES decouvertes(id) ON DELETE CASCADE,
    tags_associes VARCHAR(50)[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index
CREATE INDEX IF NOT EXISTS idx_decouvertes_scan_date ON decouvertes(scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_decouvertes_statut ON decouvertes(statut);
CREATE INDEX IF NOT EXISTS idx_decouvertes_tags ON decouvertes USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_decouvertes_source_id ON decouvertes(source_id);
CREATE INDEX IF NOT EXISTS idx_preferences_type ON preferences(type);
CREATE INDEX IF NOT EXISTS idx_sources_actif ON sources(actif);

-- Thematiques par defaut
INSERT INTO thematiques (nom) VALUES
    ('IA'),
    ('Automatisation'),
    ('E-commerce'),
    ('SaaS'),
    ('Marketing'),
    ('Video'),
    ('Metiers & RH'),
    ('Finance'),
    ('Sante'),
    ('Education'),
    ('Creation'),
    ('Innovation')
ON CONFLICT (nom) DO NOTHING;
