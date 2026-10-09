"""The ``image_hashes`` SQLite table: every picture a channel has used, by perceptual hash.

Created idempotently through ``storage/db.py``. The images stage compares each new picture
with the channel's accepted pictures from other projects (and from earlier scenes of the same
run) and regenerates with a different seed when the Hamming distance is 6 or less. Rejected
tries are stored too (``status = 'rejected'``) so the monthly budget can count every picture
the tool was paid for; only ``accepted`` rows take part in the comparison.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from ..storage import db
from .phash import hamming

IMAGE_HASHES_SQL = """
CREATE TABLE IF NOT EXISTS image_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_slug TEXT NOT NULL,
    project_id TEXT NOT NULL,
    scene INTEGER NOT NULL,
    hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    file TEXT,
    created_at TEXT NOT NULL DEFAULT ''
)
"""
IMAGE_HASHES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS image_hashes_channel ON image_hashes (channel_slug, status)"
)
COLUMNS: dict[str, str] = {
    "channel_slug": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT NOT NULL DEFAULT ''",
    "scene": "INTEGER NOT NULL DEFAULT 0",
    "hash": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'accepted'",
    "file": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}

_tables_ready: set[Path] = set()


class ImageHashRow(BaseModel):
    channel_slug: str
    project_id: str
    scene: int
    hash: str
    status: str = "accepted"
    file: str | None = None
    created_at: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def month_start_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )


def ensure_image_hashes_table(app_data_dir: Path, force: bool = False) -> None:
    """Create the table and its index if they are missing (safe to call many times)."""
    key = Path(app_data_dir)
    if key in _tables_ready and not force:
        return
    db.ensure_table(key, IMAGE_HASHES_SQL)
    with db.connect(key) as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(image_hashes)")}
        for name, decl in COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE image_hashes ADD COLUMN {name} {decl}")
        conn.execute(IMAGE_HASHES_INDEX_SQL)
    _tables_ready.add(key)


class ImageHashStore:
    def __init__(self, app_data_dir: Path | str) -> None:
        self.app_data_dir = Path(app_data_dir)
        ensure_image_hashes_table(self.app_data_dir)

    def _run(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        try:
            with db.connect(self.app_data_dir) as conn:
                return conn.execute(query, params).fetchall()
        except sqlite3.OperationalError:
            ensure_image_hashes_table(self.app_data_dir, force=True)
            with db.connect(self.app_data_dir) as conn:
                return conn.execute(query, params).fetchall()

    def accepted(self, channel_slug: str, exclude_project: str | None = None) -> list[ImageHashRow]:
        """The channel's accepted pictures, newest first, optionally without one project."""
        if exclude_project:
            rows = self._run(
                "SELECT * FROM image_hashes WHERE channel_slug = ? AND status = 'accepted' "
                "AND project_id != ? ORDER BY id DESC",
                (channel_slug, exclude_project),
            )
        else:
            rows = self._run(
                "SELECT * FROM image_hashes WHERE channel_slug = ? AND status = 'accepted' "
                "ORDER BY id DESC",
                (channel_slug,),
            )
        return [ImageHashRow.model_validate(dict(row)) for row in rows]

    def record(
        self,
        channel_slug: str,
        project_id: str,
        scene: int,
        hash_hex: str,
        *,
        status: str = "accepted",
        file: str | None = None,
    ) -> None:
        """Store one picture. An accepted picture replaces the scene's earlier accepted row."""
        if status == "accepted":
            self.forget_scene(project_id, scene)
        self._run(
            "INSERT INTO image_hashes (channel_slug, project_id, scene, hash, status, file, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (channel_slug, project_id, int(scene), hash_hex.lower(), status, file, utc_now_iso()),
        )

    def forget_scene(self, project_id: str, scene: int) -> None:
        self._run(
            "DELETE FROM image_hashes WHERE project_id = ? AND scene = ? AND status = 'accepted'",
            (project_id, scene),
        )

    def forget_project(self, project_id: str) -> int:
        rows = self._run(
            "SELECT COUNT(*) AS n FROM image_hashes WHERE project_id = ?", (project_id,)
        )
        self._run("DELETE FROM image_hashes WHERE project_id = ?", (project_id,))
        return int(rows[0]["n"]) if rows else 0

    def count_since(self, channel_slug: str, since_iso: str) -> int:
        """Pictures of every status made for the channel since ``since_iso`` (budget)."""
        rows = self._run(
            "SELECT COUNT(*) AS n FROM image_hashes WHERE channel_slug = ? AND created_at >= ?",
            (channel_slug, since_iso),
        )
        return int(rows[0]["n"]) if rows else 0

    def nearest(
        self, hash_hex: str, channel_slug: str, exclude_project: str | None = None
    ) -> tuple[int, ImageHashRow] | None:
        """``(distance, row)`` of the most similar accepted picture of the channel."""
        best: tuple[int, ImageHashRow] | None = None
        for row in self.accepted(channel_slug, exclude_project):
            try:
                distance = hamming(hash_hex, row.hash)
            except ValueError:
                continue
            if best is None or distance < best[0]:
                best = (distance, row)
                if distance == 0:
                    break
        return best
