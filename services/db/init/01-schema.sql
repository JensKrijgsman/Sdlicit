-- Sdlicit database initialization
-- Runs once on first container start

CREATE EXTENSION IF NOT EXISTS vector;

-- Workspace artefact tables
CREATE TABLE IF NOT EXISTS adrs (
    adr_id          TEXT PRIMARY KEY,
    file_path       TEXT NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed',
    domain_tag      TEXT,
    context_text    TEXT,
    decision_text   TEXT,
    alternatives_text TEXT,
    consequences_text TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    decision_vec    vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_adrs_vec ON adrs USING hnsw (decision_vec vector_cosine_ops);

CREATE TABLE IF NOT EXISTS adr_edges (
    source_adr  TEXT REFERENCES adrs(adr_id),
    target_adr  TEXT REFERENCES adrs(adr_id),
    relation    TEXT NOT NULL,
    PRIMARY KEY (source_adr, target_adr, relation)
);

CREATE TABLE IF NOT EXISTS requirements (
    req_id      TEXT PRIMARY KEY,
    domain      TEXT,
    statement   TEXT NOT NULL,
    source      TEXT,
    req_vec     vector(1536)
);

CREATE TABLE IF NOT EXISTS stories (
    story_id    TEXT PRIMARY KEY,
    persona_id  TEXT,
    statement   TEXT NOT NULL,
    cites_reqs  TEXT[]
);
