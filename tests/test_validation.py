"""Input validation regression tests. Run: python -m tests.test_validation

These exist because a hostile-input sweep against the running API found two real defects:

  * `entry_kind: "wizard"` reached SQLite, violated a CHECK constraint, and surfaced to
    the caller as a 500. A rejected request should not look like a broken server.
  * `dictionary_add` with an empty canonical succeeded, creating an entry whose canonical
    form was the empty string. That is not a memory, it is corruption, and it would then
    be indexed and scored like anything else.

Validated in app.memory rather than only at the HTTP layer, so the guarantee holds for
the evaluation harness and the seed loader too.
"""
from __future__ import annotations

import sys

from app import db as db_mod
from app.memory import ENTRY_KINDS, memory_state, observe

FAILURES: list[str] = []


def expect_rejected(conn, obs, label: str) -> None:
    try:
        observe(conn, obs)
    except (ValueError, KeyError):
        return
    FAILURES.append(f"{label}: expected rejection, but it was accepted")


def expect_accepted(conn, obs, label: str) -> None:
    try:
        observe(conn, obs)
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        FAILURES.append(f"{label}: expected success, got {type(exc).__name__}: {exc}")


def main() -> int:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["KIVI_DB_PATH"] = str(os.path.join(tmp, "validation.db"))
        db_mod.migrate(verbose=False)
        conn = db_mod.connect()

        expect_rejected(conn, {"kind": "dictionary_add", "canonical": "",
                               "entry_kind": "person"}, "empty canonical")
        expect_rejected(conn, {"kind": "dictionary_add", "canonical": "   ",
                               "entry_kind": "person"}, "whitespace canonical")
        expect_rejected(conn, {"kind": "dictionary_add", "canonical": "Zed",
                               "entry_kind": "wizard"}, "invalid entry_kind")
        expect_rejected(conn, {"kind": "correction", "before": "", "after": "X"},
                        "empty correction source")
        expect_rejected(conn, {"kind": "correction", "before": "x", "after": "  "},
                        "empty correction target")
        expect_rejected(conn, {"kind": "correction"}, "correction missing fields")
        expect_rejected(conn, {"kind": "revert", "applied": ""}, "empty revert target")
        expect_rejected(conn, {"kind": "usage", "text": ""}, "empty usage text")
        expect_rejected(conn, {"kind": "nonsense"}, "unknown observation kind")

        for kind in ENTRY_KINDS:
            expect_accepted(conn, {"kind": "dictionary_add", "canonical": f"Valid{kind}",
                                   "entry_kind": kind}, f"valid entry_kind {kind}")

        # Nothing rejected should have left a trace behind.
        canonicals = {e["canonical"] for e in memory_state(conn)}
        if "" in canonicals or "Zed" in canonicals:
            FAILURES.append("a rejected observation still created an entry")
        if len(canonicals) != len(ENTRY_KINDS):
            FAILURES.append(
                f"expected exactly {len(ENTRY_KINDS)} entries, found {len(canonicals)}: "
                f"{sorted(canonicals)}")
        conn.close()

    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ok - validation tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
