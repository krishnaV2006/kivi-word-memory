"""Three strategies, so the ablation can show what the abstraction actually bought.

  none         no memory at all -- the formatted text passes straight through
  exact_dict   the obvious implementation: exact whole-word replacement of every
               observed surface, no phonetics, no evidence, no guards
  phonetic     the system in app/resolver.py

`exact_dict` is the honest strawman. It is what most people mean by "a dictionary",
and it is genuinely good at the cases it was taught. The interesting columns are the
ones where it is not: spellings it never saw, and ordinary English words it should
have left alone.
"""
from __future__ import annotations

import re
import sqlite3

from app.resolver import resolve

STRATEGIES = ["none", "exact_dict", "phonetic"]


def _no_memory(conn: sqlite3.Connection, asr: str, formatted: str) -> tuple[str, bool]:
    text = formatted if (formatted or "").strip() else (asr or "")
    return text, False


def _exact_dictionary(conn: sqlite3.Connection, asr: str, formatted: str) -> tuple[str, bool]:
    """Whole-word, case-insensitive replacement of any observed surface with its
    canonical form. No phonetic generalisation, no evidence threshold, no guards."""
    text = formatted if (formatted or "").strip() else (asr or "")
    rows = conn.execute(
        """
        SELECT s.surface AS surface, e.canonical AS canonical
        FROM surfaces s JOIN entries e ON e.id = s.entry_id
        """
    ).fetchall()

    mapping: dict[str, str] = {}
    for r in rows:
        if r["surface"].lower() != r["canonical"].lower():
            mapping[r["surface"].lower()] = r["canonical"]

    out = text
    changed = False
    for surface, canonical in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)
        new = pattern.sub(canonical, out)
        if new != out:
            changed = True
            out = new
    return out, changed


def _phonetic(conn: sqlite3.Connection, asr: str, formatted: str) -> tuple[str, bool]:
    result = resolve(conn, asr, formatted)
    return result.memory_aware, result.intervened


RUNNERS = {
    "none": _no_memory,
    "exact_dict": _exact_dictionary,
    "phonetic": _phonetic,
}


def run(strategy: str, conn: sqlite3.Connection, asr: str, formatted: str) -> tuple[str, bool]:
    return RUNNERS[strategy](conn, asr, formatted)
