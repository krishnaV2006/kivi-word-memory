"""Entry lifecycle: what Kivi learns, when it starts acting, and when it stops.

The product decisions live here, not in the resolver. Four kinds of evidence, in
descending order of how much we trust them:

  dictionary_add  the user stated the word outright        -> trusted immediately
  correction      the user edited a transcript             -> two before we act
  usage           the user wrote the word themselves       -> weak confirmation + context
  revert          the user undid something we did          -> contradiction

Two numbers govern everything and both are deliberately small and visible:

  PROMOTION_THRESHOLD = 2   confirmations before a candidate may act
  SUPPRESSION_THRESHOLD = 2 contradictions before an entry stops acting

Why act on the second confirmation and not the first: a single edit is
indistinguishable from a typo, a one-off, or the user changing their mind. Acting on
it makes Kivi presumptuous. Waiting for a third makes it feel like it never learns.

Why suppress rather than delete: a deleted memory cannot explain itself. A suppressed
one still appears in the memory view and still reports why it declined to act, so the
user can see what Kivi believes and overrule it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.phonetics import indic_skeleton, keys_for

PROMOTION_THRESHOLD = 2
SUPPRESSION_THRESHOLD = 2
MAX_CONTEXT_TERMS = 25

# Mirrors the CHECK constraint in migration 001. Validated here rather than left to
# SQLite, because a constraint violation surfaces as an IntegrityError and reaches an
# HTTP caller as a 500 -- which reads as "this application is broken" rather than
# "you sent a bad value".
ENTRY_KINDS = ("person", "product", "term", "acronym")

# Grammatical function words only. Distinct from seed/common_words.txt, which is the
# homophone guard: "service" is an ordinary word we must never rewrite, AND a strong
# context signal for Kivi. Those are different jobs and need different lists.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "did", "do",
    "does", "for", "from", "had", "has", "have", "he", "her", "him", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "out",
    "she", "so", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "to", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "you", "your", "am", "no", "not", "all", "any",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


# --------------------------------------------------------------------------- helpers

def _now(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT datetime('now') AS t").fetchone()["t"]


def _content_tokens(text: str) -> list[str]:
    out = []
    for m in WORD_RE.finditer(text or ""):
        w = m.group(0).lower().rstrip("'’s").strip("'’-")
        if len(w) >= 3 and w not in STOPWORDS:
            out.append(w)
    return out


def find_entry(conn: sqlite3.Connection, canonical: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entries WHERE lower(canonical) = lower(?)", (canonical,)
    ).fetchone()


def _index_keys(conn: sqlite3.Connection, entry_id: int, surface: str) -> None:
    for key, algo in keys_for(surface):
        conn.execute(
            "INSERT OR IGNORE INTO phonetic_keys (entry_id, key, algo) VALUES (?, ?, ?)",
            (entry_id, key, algo),
        )


def add_surface(conn: sqlite3.Connection, entry_id: int, surface: str, origin: str) -> None:
    """Record a spelling for this entry and index its phonetic keys.

    Indexing every observed surface widens the net: two different misspellings of one
    name each contribute their own keys, so the entry becomes easier to find over time.
    """
    conn.execute(
        """
        INSERT INTO surfaces (entry_id, surface, origin, count) VALUES (?, ?, ?, 1)
        ON CONFLICT (entry_id, surface) DO UPDATE SET count = count + 1
        """,
        (entry_id, surface, origin),
    )
    _index_keys(conn, entry_id, surface)


def create_entry(conn: sqlite3.Connection, canonical: str, kind: str) -> int:
    now = _now(conn)
    cur = conn.execute(
        """
        INSERT INTO entries (canonical, kind, status, confidence, protected, created_at, updated_at)
        VALUES (?, ?, 'candidate', 0.0, 0, ?, ?)
        """,
        (canonical, kind, now, now),
    )
    entry_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO evidence (entry_id, first_seen, last_seen) VALUES (?, ?, ?)",
        (entry_id, now, now),
    )
    add_surface(conn, entry_id, canonical, "canonical")
    return entry_id


def get_or_create(conn: sqlite3.Connection, canonical: str, kind: str) -> int:
    row = find_entry(conn, canonical)
    return int(row["id"]) if row else create_entry(conn, canonical, kind)


def _bump(conn: sqlite3.Connection, entry_id: int, field: str, amount: int = 1) -> None:
    conn.execute(
        f"UPDATE evidence SET {field} = {field} + ?, last_seen = ? WHERE entry_id = ?",
        (amount, _now(conn), entry_id),
    )


def recompute_status(conn: sqlite3.Connection, entry_id: int) -> None:
    """Derive confidence and status from the evidence. Pure function of the log."""
    ev = conn.execute("SELECT * FROM evidence WHERE entry_id = ?", (entry_id,)).fetchone()
    entry = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if ev is None or entry is None:
        return
    conf_n, contra_n = ev["confirmations"], ev["contradictions"]
    protected = bool(entry["protected"])

    # Laplace-smoothed agreement rate: never fully certain, never fully condemned.
    confidence = (conf_n + 1.0) / (conf_n + contra_n + 2.0)
    if protected:
        confidence = max(confidence, 0.95)

    if protected:
        status = "active"                       # a direct instruction outranks inference
    elif contra_n >= SUPPRESSION_THRESHOLD:
        status = "suppressed"
    elif conf_n >= PROMOTION_THRESHOLD:
        status = "active"
    else:
        status = "candidate"

    conn.execute(
        "UPDATE entries SET confidence = ?, status = ?, updated_at = ? WHERE id = ?",
        (round(confidence, 4), status, _now(conn), entry_id),
    )


def _mine_context(conn: sqlite3.Connection, entry_id: int, text: str, canonical: str) -> None:
    """Learn the words a memory tends to live near.

    This is what later separates the product from the fruit. Terms are matched
    phonetically at lookup time, so a context term learned as 'sarvam' still fires when
    the ASR writes 'sarwam'.
    """
    own = indic_skeleton(canonical)
    for term in _content_tokens(text):
        if indic_skeleton(term) == own:
            continue
        conn.execute(
            """
            INSERT INTO context_terms (entry_id, term, weight) VALUES (?, ?, 1.0)
            ON CONFLICT (entry_id, term) DO UPDATE SET weight = weight + 1.0
            """,
            (entry_id, term),
        )
    # Bounded growth: keep the strongest terms only.
    conn.execute(
        """
        DELETE FROM context_terms WHERE entry_id = ? AND term NOT IN (
            SELECT term FROM context_terms WHERE entry_id = ?
            ORDER BY weight DESC, term ASC LIMIT ?
        )
        """,
        (entry_id, entry_id, MAX_CONTEXT_TERMS),
    )


def _log(conn: sqlite3.Connection, kind: str, payload: dict, entry_id: int | None) -> None:
    conn.execute(
        "INSERT INTO observations (kind, payload, entry_id, created_at) VALUES (?, ?, ?, ?)",
        (kind, json.dumps(payload, ensure_ascii=False), entry_id, _now(conn)),
    )


# ------------------------------------------------------------------- observation kinds

def _require_word(obs: dict, field: str) -> str:
    """A memory entry with a blank canonical form is not a memory, it is corruption."""
    value = (obs.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field!r} must be a non-empty word")
    return value


def _require_kind(obs: dict) -> str:
    kind = obs.get("entry_kind") or "term"
    if kind not in ENTRY_KINDS:
        raise ValueError(f"entry_kind must be one of {', '.join(ENTRY_KINDS)}; got {kind!r}")
    return kind


def observe(conn: sqlite3.Connection, obs: dict[str, Any]) -> dict[str, Any]:
    """Apply one observation. Returns a short description of what changed."""
    kind = obs.get("kind")
    handler = {
        "correction": _observe_correction,
        "dictionary_add": _observe_dictionary_add,
        "usage": _observe_usage,
        "revert": _observe_revert,
    }.get(kind)
    if handler is None:
        raise ValueError(f"unknown observation kind: {kind!r}")
    result = handler(conn, obs)
    conn.commit()
    return result


def _observe_correction(conn: sqlite3.Connection, obs: dict) -> dict:
    before, after = _require_word(obs, "before"), _require_word(obs, "after")
    kind = _require_kind(obs)
    entry_id = get_or_create(conn, after, kind)

    add_surface(conn, entry_id, after, "canonical")
    if indic_skeleton(before) != indic_skeleton(after):
        add_surface(conn, entry_id, before, "observed")
    else:
        # Same sound, different spelling: still worth indexing as a surface.
        add_surface(conn, entry_id, before, "observed")

    _bump(conn, entry_id, "confirmations")
    if obs.get("context"):
        _mine_context(conn, entry_id, obs["context"], after)
    recompute_status(conn, entry_id)
    _log(conn, "correction", obs, entry_id)

    entry = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return {"entry_id": entry_id, "canonical": after, "status": entry["status"]}


def _observe_dictionary_add(conn: sqlite3.Connection, obs: dict) -> dict:
    canonical = _require_word(obs, "canonical")
    kind = _require_kind(obs)
    entry_id = get_or_create(conn, canonical, kind)
    add_surface(conn, entry_id, canonical, "canonical")

    # A direct instruction is worth the promotion threshold on its own, and pins the
    # entry so that later inferred contradictions cannot silently overturn it.
    ev = conn.execute("SELECT * FROM evidence WHERE entry_id = ?", (entry_id,)).fetchone()
    if ev["confirmations"] < PROMOTION_THRESHOLD:
        _bump(conn, entry_id, "confirmations", PROMOTION_THRESHOLD - ev["confirmations"])
    conn.execute("UPDATE entries SET protected = 1 WHERE id = ?", (entry_id,))
    recompute_status(conn, entry_id)
    _log(conn, "dictionary_add", obs, entry_id)

    entry = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return {"entry_id": entry_id, "canonical": canonical, "status": entry["status"]}


def _observe_usage(conn: sqlite3.Connection, obs: dict) -> dict:
    """The user wrote this text themselves, so every word in it is spelled how they
    want. Weak confirmation for any memory it mentions, plus context for all of them."""
    text = _require_word(obs, "text")
    touched: list[str] = []
    tokens = {t for t in _content_tokens(text)}
    skeletons = {indic_skeleton(t) for t in tokens}

    for entry in conn.execute("SELECT * FROM entries").fetchall():
        if indic_skeleton(entry["canonical"]) in skeletons:
            _bump(conn, entry["id"], "confirmations")
            _mine_context(conn, entry["id"], text, entry["canonical"])
            recompute_status(conn, entry["id"])
            touched.append(entry["canonical"])

    _log(conn, "usage", obs, None)
    return {"touched": touched}


def _observe_revert(conn: sqlite3.Connection, obs: dict) -> dict:
    """The user undid something we did. The strongest negative signal available."""
    applied = _require_word(obs, "applied")
    entry = find_entry(conn, applied)
    if entry is None:
        _log(conn, "revert", obs, None)
        return {"entry_id": None, "note": "no such entry"}

    _bump(conn, entry["id"], "contradictions")
    recompute_status(conn, entry["id"])
    _log(conn, "revert", obs, entry["id"])

    updated = conn.execute("SELECT * FROM entries WHERE id = ?", (entry["id"],)).fetchone()
    return {"entry_id": entry["id"], "canonical": applied, "status": updated["status"]}


# ------------------------------------------------------------------- state inspection

def memory_state(conn: sqlite3.Connection) -> list[dict]:
    """Everything the system believes, in a form a person can read."""
    out = []
    for e in conn.execute("SELECT * FROM entries ORDER BY canonical").fetchall():
        ev = conn.execute("SELECT * FROM evidence WHERE entry_id = ?", (e["id"],)).fetchone()
        surfaces = conn.execute(
            "SELECT surface, origin, count FROM surfaces WHERE entry_id = ? ORDER BY origin, surface",
            (e["id"],),
        ).fetchall()
        keys = conn.execute(
            "SELECT key, algo FROM phonetic_keys WHERE entry_id = ? ORDER BY algo, key", (e["id"],)
        ).fetchall()
        ctx = conn.execute(
            "SELECT term, weight FROM context_terms WHERE entry_id = ? ORDER BY weight DESC, term LIMIT 12",
            (e["id"],),
        ).fetchall()
        out.append({
            "id": e["id"],
            "canonical": e["canonical"],
            "kind": e["kind"],
            "status": e["status"],
            "confidence": e["confidence"],
            "protected": bool(e["protected"]),
            "evidence": {
                "confirmations": ev["confirmations"] if ev else 0,
                "contradictions": ev["contradictions"] if ev else 0,
                "applications": ev["applications"] if ev else 0,
            },
            "surfaces": [dict(s) for s in surfaces],
            "phonetic_keys": [dict(k) for k in keys],
            "context_terms": [dict(c) for c in ctx],
        })
    return out


def set_protected(conn: sqlite3.Connection, entry_id: int, protected: bool) -> None:
    conn.execute("UPDATE entries SET protected = ? WHERE id = ?", (1 if protected else 0, entry_id))
    recompute_status(conn, entry_id)
    conn.commit()


def force_status(conn: sqlite3.Connection, entry_id: int, status: str) -> None:
    """Manual override from the memory view, e.g. suppressing an entry by hand."""
    if status not in ("candidate", "active", "suppressed"):
        raise ValueError(f"bad status: {status}")
    protected = 0 if status == "suppressed" else None
    if protected is not None:
        conn.execute("UPDATE entries SET protected = 0 WHERE id = ?", (entry_id,))
    conn.execute(
        "UPDATE entries SET status = ?, updated_at = ? WHERE id = ?", (status, _now(conn), entry_id)
    )
    conn.commit()


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()


def reindex(conn: sqlite3.Connection) -> int:
    """Recompute every phonetic key from the surfaces already stored.

    Needed when a new key algorithm is added: migration 003 widens the schema, but the
    keys themselves are computed in Python, so a database created before that migration
    has entries with no fuzzy keys. A fresh migrate + seed does not need this.
    """
    n = 0
    for row in conn.execute("SELECT entry_id, surface FROM surfaces").fetchall():
        _index_keys(conn, row["entry_id"], row["surface"])
        n += 1
    conn.commit()
    return n


def note_application(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute(
        "UPDATE evidence SET applications = applications + 1 WHERE entry_id = ?", (entry_id,)
    )
    conn.execute(
        "UPDATE entries SET last_applied_at = ? WHERE id = ?", (_now(conn), entry_id)
    )
