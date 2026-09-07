-- 003_fuzzy_keys.sql
-- Add the 'indic_fuzzy' retrieval tier, which folds b/v/w together so that ASR returning
-- 'Bekariya' for 'Vekariya' can still find the entry. We refuse to fold b and v in the
-- ordinary skeleton because that would also collapse bat/vat and ban/van, so the fold
-- gets its own tier that the resolver only trusts when the sentence supplies independent
-- context. See DISCOVERIES.md section 7.
--
-- SQLite cannot ALTER a CHECK constraint, so the table is rebuilt in place. Existing rows
-- are preserved; the new fuzzy keys themselves are written by the application, so a
-- database migrated from an earlier version needs `python -m app.db reindex` to backfill
-- them. A fresh migrate + seed produces them automatically.

CREATE TABLE phonetic_keys_new (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    key      TEXT    NOT NULL,
    algo     TEXT    NOT NULL CHECK (algo IN ('indic','indic_loose','indic_fuzzy','metaphone')),
    PRIMARY KEY (entry_id, key, algo)
);

INSERT INTO phonetic_keys_new (entry_id, key, algo)
    SELECT entry_id, key, algo FROM phonetic_keys;

DROP TABLE phonetic_keys;

ALTER TABLE phonetic_keys_new RENAME TO phonetic_keys;

CREATE INDEX idx_phonetic_keys_lookup ON phonetic_keys(key, algo);
