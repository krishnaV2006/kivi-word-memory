-- 001_init.sql — Kivi word-level phonetic memory
-- Core principle: an entry is a phonetic identity, not a string replacement.

CREATE TABLE entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical       TEXT    NOT NULL,           -- the surface form the user expects: "Aaditya"
    kind            TEXT    NOT NULL CHECK (kind IN ('person','product','term','acronym')),
    status          TEXT    NOT NULL CHECK (status IN ('candidate','active','suppressed')),
    confidence      REAL    NOT NULL DEFAULT 0.0,
    protected       INTEGER NOT NULL DEFAULT 0, -- user-pinned: never auto-suppressed
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    last_applied_at TEXT
);
CREATE UNIQUE INDEX idx_entries_canonical ON entries(canonical, kind);

-- Variant spellings that map to an entry. origin='canonical' is the entry's own form,
-- 'observed' came from a real correction, 'generated' was derived by us.
CREATE TABLE surfaces (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    surface  TEXT    NOT NULL,
    origin   TEXT    NOT NULL CHECK (origin IN ('canonical','observed','generated')),
    count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (entry_id, surface)
);
CREATE INDEX idx_surfaces_surface ON surfaces(surface);

-- THE index that makes generalisation work. A variant never observed still finds its
-- entry because it collapses to the same key.
CREATE TABLE phonetic_keys (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    key      TEXT    NOT NULL,
    algo     TEXT    NOT NULL CHECK (algo IN ('indic','indic_loose','metaphone')),
    PRIMARY KEY (entry_id, key, algo)
);
CREATE INDEX idx_phonetic_keys_lookup ON phonetic_keys(key, algo);

-- Append-only evidence log. Never mutated, never deleted except by reset.
CREATE TABLE observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL CHECK (kind IN ('correction','dictionary_add','usage','revert')),
    payload    TEXT    NOT NULL,               -- JSON
    entry_id   INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX idx_observations_entry ON observations(entry_id);

-- Aggregated evidence per entry, derived from observations.
CREATE TABLE evidence (
    entry_id        INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    confirmations   INTEGER NOT NULL DEFAULT 0,
    contradictions  INTEGER NOT NULL DEFAULT 0,
    applications    INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT
);

-- Decision trace: one row per span considered, whether or not we intervened.
-- This is how "why did it (not) intervene" stays inspectable after the fact.
CREATE TABLE decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT    NOT NULL,
    span       TEXT    NOT NULL,
    entry_id   INTEGER,
    action     TEXT    NOT NULL,
    reason     TEXT    NOT NULL,
    score      REAL,
    runner_up  REAL,
    created_at TEXT    NOT NULL
);
CREATE INDEX idx_decisions_request ON decisions(request_id);

-- Homophone guard. "kiwi" is a fruit far more often than it is a product.
CREATE TABLE common_words (
    word TEXT PRIMARY KEY
);
