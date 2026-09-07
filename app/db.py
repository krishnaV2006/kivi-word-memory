"""SQLite access, migration runner and reset.

Deliberately dependency-free: the stdlib sqlite3 driver plus hand-written SQL files.
An ORM would be more code and more magic for a schema this small, and migrations that
a reviewer can read as plain SQL are easier to trust than generated ones.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"

DEFAULT_DB_PATH = REPO_ROOT / "kivi.db"


def db_path() -> Path:
    """Resolve the database location. KIVI_DB_PATH overrides, for tests and eval."""
    raw = os.environ.get("KIVI_DB_PATH")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DB_PATH


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Write-ahead logging, because every resolution commits a decision trace and the
    # default rollback journal makes that commit the dominant cost of a request
    # (measured: 12.8 ms -> 5.5 ms). synchronous=NORMAL under WAL survives application
    # crashes and risks only the most recent commits on OS or power failure, which is
    # the right trade for an inspection log.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    _ensure_migrations_table(conn)
    return {r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations")}


def migrate(conn: sqlite3.Connection | None = None, verbose: bool = True) -> list[str]:
    """Apply every unapplied migration in filename order. Idempotent."""
    own = conn is None
    conn = conn or connect()
    try:
        done = applied_migrations(conn)
        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in done)
        for path in pending:
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
                (path.name,),
            )
            conn.commit()
            if verbose:
                print(f"applied {path.name}")
        if verbose and not pending:
            print("no pending migrations")
        return [p.name for p in pending]
    finally:
        if own:
            conn.close()


def reset(verbose: bool = True) -> None:
    """Delete the database file and re-migrate. The documented reset procedure."""
    target = db_path()
    # WAL leaves -wal and -shm beside the database. A reset that removed only the main
    # file would leave committed-but-uncheckpointed pages behind.
    for path in (target, target.with_name(target.name + "-wal"),
                 target.with_name(target.name + "-shm")):
        if path.exists():
            path.unlink()
            if verbose:
                print(f"removed {path.name}")
    migrate(verbose=verbose)


def db_stats(conn: sqlite3.Connection) -> dict:
    """Row counts per table plus file size, for the growth measurements in the eval."""
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    rows = {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] for t in tables}
    target = db_path()
    return {
        "rows": rows,
        "total_rows": sum(rows.values()),
        "bytes": target.stat().st_size if target.exists() else 0,
    }


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "migrate"
    if cmd == "migrate":
        migrate()
    elif cmd == "reset":
        reset()
    elif cmd == "reindex":
        from app.memory import reindex

        conn = connect()
        try:
            print(f"reindexed {reindex(conn)} surfaces")
        finally:
            conn.close()
    elif cmd == "status":
        conn = connect()
        try:
            print(f"database: {db_path()}")
            for name in sorted(applied_migrations(conn)):
                print(f"  applied: {name}")
            stats = db_stats(conn)
            print(f"  rows: {stats['total_rows']}  bytes: {stats['bytes']}")
        finally:
            conn.close()
    else:
        print(f"unknown command: {cmd}\nusage: python -m app.db [migrate|reset|reindex|status]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
