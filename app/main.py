"""HTTP surface. Run: python -m uvicorn app.main:app --port 8000

Thin by design -- every route is a few lines over app.memory and app.resolver, so the
same core is reachable from the eval harness without going through HTTP.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db as db_mod
from app import memory as mem
from app.resolver import resolve

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Kivi word memory", version="1.0.0")


class ObservationIn(BaseModel):
    kind: str = Field(description="correction | dictionary_add | usage | revert")
    before: str | None = None
    after: str | None = None
    canonical: str | None = None
    text: str | None = None
    applied: str | None = None
    reverted_to: str | None = None
    entry_kind: str | None = None
    context: str | None = None


class ResolveIn(BaseModel):
    asr: str = ""
    formatted: str = ""


class StatusIn(BaseModel):
    status: str


def _conn():
    return db_mod.connect()


@app.on_event("startup")
def _startup() -> None:
    db_mod.migrate(verbose=False)


@app.get("/api/health")
def health() -> dict:
    conn = _conn()
    try:
        stats = db_mod.db_stats(conn)
        n_active = conn.execute(
            "SELECT COUNT(*) AS c FROM entries WHERE status = 'active'"
        ).fetchone()["c"]
        return {"ok": True, "active_entries": n_active, "db": stats}
    finally:
        conn.close()


@app.post("/api/observe")
def post_observe(obs: ObservationIn) -> dict:
    payload = {k: v for k, v in obs.model_dump().items() if v is not None}
    conn = _conn()
    try:
        return {"ok": True, "result": mem.observe(conn, payload)}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        # Backstop. Validation above should catch bad input first; if a constraint still
        # fires, that is a rejected request, not a broken server.
        raise HTTPException(status_code=400, detail=f"rejected by schema: {exc}") from exc
    finally:
        conn.close()


@app.get("/api/memory")
def get_memory() -> dict:
    conn = _conn()
    try:
        return {"entries": mem.memory_state(conn), "db": db_mod.db_stats(conn)}
    finally:
        conn.close()


@app.post("/api/memory/{entry_id}/status")
def post_status(entry_id: int, body: StatusIn) -> dict:
    conn = _conn()
    try:
        mem.force_status(conn, entry_id, body.status)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/memory/{entry_id}/pin")
def post_pin(entry_id: int, protected: bool = True) -> dict:
    conn = _conn()
    try:
        mem.set_protected(conn, entry_id, protected)
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/memory/{entry_id}")
def delete_memory(entry_id: int) -> dict:
    conn = _conn()
    try:
        mem.delete_entry(conn, entry_id)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/resolve")
def post_resolve(body: ResolveIn) -> JSONResponse:
    conn = _conn()
    try:
        result = resolve(conn, body.asr, body.formatted)
        return JSONResponse(result.to_dict())
    finally:
        conn.close()


@app.post("/api/reset")
def post_reset() -> dict:
    """Drop everything, re-migrate, re-seed. The documented reset path."""
    from seed.seed import seed as run_seed

    db_mod.reset(verbose=False)
    conn = _conn()
    try:
        run_seed(conn, verbose=False)
        return {"ok": True, "entries": len(mem.memory_state(conn))}
    finally:
        conn.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
