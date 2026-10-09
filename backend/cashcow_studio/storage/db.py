"""SQLite helper. One file per user at ``<app_data_dir>/db.sqlite``.

Every module that needs a table calls ``ensure_table`` with its own ``CREATE TABLE IF NOT
EXISTS`` statement at start-up; there is no central migration list in this milestone. Use
``connect()`` as a context manager; it enables WAL mode and foreign keys and commits on exit.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_FILE = "db.sqlite"
_lock = threading.RLock()


def db_path(app_data_dir: Path) -> Path:
    return Path(app_data_dir) / DB_FILE


@contextmanager
def connect(app_data_dir: Path) -> Iterator[sqlite3.Connection]:
    path = db_path(app_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def ensure_table(app_data_dir: Path, create_sql: str) -> None:
    """Run one ``CREATE TABLE IF NOT EXISTS ...`` (or index) statement."""
    with connect(app_data_dir) as conn:
        conn.execute(create_sql)
