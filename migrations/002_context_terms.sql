-- 002_context_terms.sql
-- Static common-word guards are blunt. Context is the better signal: "kiwi" next to
-- "sarvam" or "service" is the product; "kiwi" next to "breakfast" is the fruit.
-- Terms are mined from usage observations rather than hand-written.

CREATE TABLE context_terms (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    term     TEXT    NOT NULL,
    weight   REAL    NOT NULL DEFAULT 1.0,
    PRIMARY KEY (entry_id, term)
);
CREATE INDEX idx_context_terms_term ON context_terms(term);
