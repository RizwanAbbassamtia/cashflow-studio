"""The ``llm_calls`` table: one row per Claude call, with tokens and cost. Never the prompt."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..storage import db

CREATE_LLM_CALLS = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    stage TEXT,
    task TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    requested_model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    request_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT ''
)
"""
CREATE_LLM_CALLS_INDEX = (
    "CREATE INDEX IF NOT EXISTS llm_calls_project ON llm_calls (project_id, created_at)"
)

# Column name -> declaration, used both for inserts and to upgrade a table another module made.
COLUMNS: dict[str, str] = {
    "project_id": "TEXT",
    "stage": "TEXT",
    "task": "TEXT NOT NULL DEFAULT ''",
    "model": "TEXT NOT NULL DEFAULT ''",
    "requested_model": "TEXT",
    "input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "output_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_read_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_creation_input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cost_usd": "REAL NOT NULL DEFAULT 0",
    "duration_ms": "INTEGER NOT NULL DEFAULT 0",
    "status": "TEXT NOT NULL DEFAULT 'ok'",
    "request_id": "TEXT",
    "error": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}
INT_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "duration_ms",
)


def ensure_llm_calls_table(app_data_dir: Path) -> None:
    db.ensure_table(app_data_dir, CREATE_LLM_CALLS)
    with db.connect(app_data_dir) as conn:
        add_missing_columns(conn, "llm_calls", COLUMNS)
        conn.execute(CREATE_LLM_CALLS_INDEX)


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Another module may have created an older ``table``; add the columns this one needs."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def log_llm_call(app_data_dir: Path, record: dict[str, Any]) -> None:
    """Insert one row. ``record`` keys are a subset of :data:`COLUMNS`; extras are ignored."""
    row = {name: record.get(name) for name in COLUMNS}
    row["created_at"] = row.get("created_at") or utc_now_iso()
    row["status"] = row.get("status") or "ok"
    row["task"] = row.get("task") or ""
    row["model"] = row.get("model") or ""
    for name in INT_COLUMNS:
        row[name] = int(row.get(name) or 0)
    row["cost_usd"] = float(row.get("cost_usd") or 0.0)
    names = ", ".join(COLUMNS)
    placeholders = ", ".join(f":{name}" for name in COLUMNS)
    with db.connect(app_data_dir) as conn:
        conn.execute(f"INSERT INTO llm_calls ({names}) VALUES ({placeholders})", row)


def list_llm_calls(
    app_data_dir: Path, project_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    ensure_llm_calls_table(app_data_dir)
    with db.connect(app_data_dir) as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM llm_calls WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def project_llm_cost(app_data_dir: Path, project_id: str) -> float:
    ensure_llm_calls_table(app_data_dir)
    with db.connect(app_data_dir) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_calls WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return round(float(row["total"] if row else 0.0), 6)
