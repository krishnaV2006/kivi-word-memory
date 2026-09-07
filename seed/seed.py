"""Load reproducible seed data. Run: python -m seed.seed

Memory is LEARNED, never inserted. The seed replays a transcript of ordinary use --
corrections the user made, sentences they typed -- and lets the lifecycle rules decide
what becomes a memory and what stays a hypothesis. Seeding by writing rows straight
into `entries` would let the seed express states the product can never actually reach.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from app import db as db_mod
from app.memory import observe

SEED_DIR = Path(__file__).resolve().parent
SEED_FILE = SEED_DIR / "seed.json"
COMMON_WORDS_FILE = SEED_DIR / "common_words.txt"


def load_common_words(conn: sqlite3.Connection) -> int:
    words = []
    for line in COMMON_WORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            words.append((line,))
    conn.executemany("INSERT OR IGNORE INTO common_words (word) VALUES (?)", words)
    conn.commit()
    return len(words)


def load_observations(conn: sqlite3.Connection, observations: list[dict]) -> None:
    for obs in observations:
        observe(conn, obs)


def seed(conn: sqlite3.Connection | None = None, verbose: bool = True) -> None:
    own = conn is None
    conn = conn or db_mod.connect()
    try:
        data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        n_words = load_common_words(conn)
        load_observations(conn, data["observations"])
        if verbose:
            entries = conn.execute("SELECT status, COUNT(*) AS c FROM entries GROUP BY status").fetchall()
            summary = ", ".join(f"{r['c']} {r['status']}" for r in entries) or "no entries"
            print(f"seeded {len(data['observations'])} observations -> {summary}")
            print(f"loaded {n_words} common words for the homophone guard")
    finally:
        if own:
            conn.close()


def main() -> int:
    db_mod.migrate(verbose=False)
    seed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
