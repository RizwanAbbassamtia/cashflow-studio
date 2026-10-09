"""Project folders under ``<projects_dir>`` and their index in SQLite.

Layout (docs/M1-M2-CONTRACT.md section 1)::

    <projects_dir>/<YYYY-MM-DD>_<channel-slug>_<topic-slug>/
        job.json                 the Project model (the truth)
        01_research/ 02_title/ 03_script/ 04_storyboard/
        05_voice/ 06_images/ 07_edit/ 08_export/
    <projects_dir>/_archived/<folder-name>[-2]/      archived projects, never deleted

The files are the truth: ``job.json`` is written atomically on every change, and the SQLite
table ``projects`` (id, channel_slug, folder, status json, updated_at) is only an index that
is rebuilt from the folders whenever they disagree. Every check-then-write runs under
:data:`LOCK` because the API serves requests from several threads.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from slugify import slugify

from ..models.project import Project, ProjectSummary, StageName, status_of, summary_of
from . import db
from .settings_store import atomic_write_text

JOB_FILE = "job.json"
ARCHIVE_DIR = "_archived"
STAGE_DIRS: dict[StageName, str] = {
    StageName.research: "01_research",
    StageName.title: "02_title",
    StageName.script: "03_script",
    StageName.storyboard: "04_storyboard",
    StageName.voice: "05_voice",
    StageName.images: "06_images",
    StageName.edit: "07_edit",
    StageName.export: "08_export",
}
# Keeps <projects_dir>/<date>_<80-char channel>_<40-char topic>/04_storyboard/<file> well under
# the 260-character Windows path limit for the default projects folder.
MAX_TOPIC_SLUG_LENGTH = 40

LOCK = threading.RLock()

PROJECTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    channel_slug TEXT NOT NULL,
    folder TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
PROJECTS_INDEX_SQL = "CREATE INDEX IF NOT EXISTS projects_channel ON projects (channel_slug)"
UPSERT_SQL = (
    "INSERT INTO projects (id, channel_slug, folder, status, updated_at) "
    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
    "channel_slug = excluded.channel_slug, folder = excluded.folder, "
    "status = excluded.status, updated_at = excluded.updated_at"
)

_tables_ready: set[Path] = set()


class ProjectStoreError(Exception):
    """Base class; the message is plain English and safe to show to the user."""


class ProjectNotFound(ProjectStoreError):
    pass


class ProjectFolderBusy(ProjectStoreError):
    """The folder cannot be moved because a file inside it is still open (a render, a
    conversion or a scan is still running)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def topic_slug(text: str | None, fallback: str = "topic") -> str:
    """Folder-safe form of a topic or title, at most 40 characters."""
    slug = slugify(text or "", max_length=MAX_TOPIC_SLUG_LENGTH, word_boundary=True)
    return slug or fallback


def folder_name(channel_slug: str, topic: str, created: datetime | None = None) -> str:
    stamp = (created or utc_now()).strftime("%Y-%m-%d")
    return f"{stamp}_{channel_slug}_{topic_slug(topic)}"


class ProjectStore:
    """Reads and writes ``job.json`` files and keeps the SQLite index in step."""

    def __init__(self, projects_dir: Path | str, app_data_dir: Path | str) -> None:
        self.projects_dir = Path(projects_dir)
        self.app_data_dir = Path(app_data_dir)
        self._ensure_table()

    # Paths ----------------------------------------------------------------------------

    @property
    def archive_dir(self) -> Path:
        return self.projects_dir / ARCHIVE_DIR

    def new_folder(self, channel_slug: str, topic: str, created: datetime | None = None) -> Path:
        """A folder path that does not exist yet: ``<date>_<channel>_<topic>[-2, -3...]``."""
        base = self.projects_dir / folder_name(channel_slug, topic, created)
        candidate = base
        counter = 2
        while candidate.exists() or (self.archive_dir / candidate.name).exists():
            candidate = base.with_name(f"{base.name}-{counter}")
            counter += 1
        return candidate

    @staticmethod
    def job_file(folder: Path | str) -> Path:
        return Path(folder) / JOB_FILE

    # CRUD -----------------------------------------------------------------------------

    def create(self, project: Project) -> Project:
        """Create the folder with its eight stage subfolders and write ``job.json``.

        ``project.folder`` must already be set (see :meth:`new_folder`).
        """
        folder = Path(project.folder)
        with LOCK:
            if self.job_file(folder).exists():
                raise ProjectStoreError(f"The folder {folder} already holds a project.")
            folder.mkdir(parents=True, exist_ok=True)
            for name in STAGE_DIRS.values():
                (folder / name).mkdir(exist_ok=True)
            self._write(project)
            self._index(project)
        return project

    def save(self, project: Project) -> Project:
        """Write ``job.json`` atomically and refresh the index row. Sets ``updated_at``."""
        project.updated_at = utc_now()
        with LOCK:
            self._write(project)
            self._index(project)
        return project

    def get(self, project_id: str) -> Project:
        folder = self._folder_from_index(project_id)
        if folder is not None:
            project = self._read(folder)
            if project is not None and project.id == project_id:
                return project
        # The index is stale (folder moved by hand, database rebuilt): look at the files.
        for project in self._scan():
            if project.id == project_id:
                self._index(project)
                return project
        with LOCK, db.connect(self.app_data_dir) as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        raise ProjectNotFound("That project no longer exists. It may have been archived.")

    def list(
        self, channel_slug: str | None = None, status: str | None = None
    ) -> list[ProjectSummary]:
        """Every project folder, newest first. Rebuilds the index as a side effect.

        The index is brought in step in one transaction: only rows whose ``updated_at`` or
        folder differs from the file are written, so listing a few hundred projects costs one
        database round trip, not one fsync per project.
        """
        projects: list[Project] = []
        seen: set[str] = set()
        for project in self._scan():
            if project.id in seen:
                continue  # a copied folder: the first one wins
            seen.add(project.id)
            projects.append(project)
        with LOCK:
            try:
                self._sync_index(projects)
            except sqlite3.OperationalError:
                self._ensure_table(force=True)
                self._sync_index(projects)
        rows: list[ProjectSummary] = []
        for project in projects:
            if channel_slug and project.channel_slug != channel_slug:
                continue
            if status and status_of(project) != status:
                continue
            rows.append(summary_of(project))
        rows.sort(key=lambda row: row.updated_at, reverse=True)
        return rows

    def rename(self, project: Project, topic: str) -> bool:
        """Give the project folder its final name ``<date>_<channel>_<topic-slug>``.

        Used once research has learned the video's title, so the folder in Explorer says
        which video it holds instead of ``ai-pick``. Nothing may be writing into the folder at
        that moment (the engine calls this between stages). Returns ``False`` and keeps the
        old name when the folder cannot be moved (a file is open in another program) or the
        name would not change.
        """
        slug = topic_slug(topic, fallback=project.topic_slug)
        old = Path(project.folder)
        with LOCK:
            if slug == project.topic_slug or not old.is_dir():
                return False
            target = self.new_folder(project.channel_slug, topic, project.created_at)
            if target.name == old.name:
                return False
            try:
                old.rename(target)
            except OSError:
                return False
            project.folder = str(target)
            project.topic_slug = slug
            self._write(project)
            self._index(project)
        return True

    def archive(self, project_id: str) -> Path:
        """Move the project folder to ``_archived/`` and drop its index row. Never deletes.

        The move is a plain rename, which is all-or-nothing. When Windows refuses it
        because a file inside is still open (FFmpeg is still writing a render, say) the
        archive is refused with :class:`ProjectFolderBusy` and nothing changes; a copy-and-
        delete fallback would leave a half-copied project behind. Only when the archive
        folder sits on another drive is the folder copied over.
        """
        project = self.get(project_id)
        folder = Path(project.folder)
        with LOCK:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            destination = self.archive_dir / folder.name
            counter = 2
            while destination.exists():
                destination = self.archive_dir / f"{folder.name}-{counter}"
                counter += 1
            try:
                os.rename(folder, destination)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    shutil.move(str(folder), str(destination))
                else:
                    raise ProjectFolderBusy(
                        f"The project folder {folder.name} is still in use (a step is still "
                        "writing to it). Wait until the step stops, then archive it again."
                    ) from exc
            with db.connect(self.app_data_dir) as conn:
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return destination

    def reindex(self) -> int:
        """Rebuild the ``projects`` table from the folders; returns the number of projects."""
        return len(self.list())

    # Internals ------------------------------------------------------------------------

    def _ensure_table(self, force: bool = False) -> None:
        key = self.app_data_dir
        if key in _tables_ready and not force:
            return
        db.ensure_table(self.app_data_dir, PROJECTS_TABLE_SQL)
        db.ensure_table(self.app_data_dir, PROJECTS_INDEX_SQL)
        _tables_ready.add(key)

    @staticmethod
    def _index_params(project: Project) -> tuple[str, str, str, str, str]:
        summary = summary_of(project)
        return (
            project.id,
            project.channel_slug,
            str(project.folder),
            json.dumps(summary.model_dump(mode="json")),
            project.updated_at.isoformat(),
        )

    def _index_row(self, project: Project) -> None:
        with db.connect(self.app_data_dir) as conn:
            conn.execute(UPSERT_SQL, self._index_params(project))

    def _sync_index(self, projects: list[Project]) -> None:
        """One transaction: drop rows without a folder, upsert rows that changed."""
        with db.connect(self.app_data_dir) as conn:
            known = {
                row["id"]: (row["updated_at"], row["folder"])
                for row in conn.execute("SELECT id, updated_at, folder FROM projects")
            }
            stale = set(known) - {project.id for project in projects}
            if stale:
                conn.executemany(
                    "DELETE FROM projects WHERE id = ?", [(project_id,) for project_id in stale]
                )
            changed = [
                self._index_params(project)
                for project in projects
                if known.get(project.id) != (project.updated_at.isoformat(), str(project.folder))
            ]
            if changed:
                conn.executemany(UPSERT_SQL, changed)

    def _write(self, project: Project) -> None:
        atomic_write_text(self.job_file(project.folder), project.model_dump_json(indent=2) + "\n")

    def _read(self, folder: Path) -> Project | None:
        path = self.job_file(folder)
        try:
            return Project.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def _scan(self) -> Iterator[Project]:
        """Every readable ``job.json`` directly under the projects folder (not the archive)."""
        if not self.projects_dir.is_dir():
            return
        for folder in sorted(self.projects_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            project = self._read(folder)
            if project is None:
                continue
            if Path(project.folder) != folder:
                # Moved or copied by hand: the folder it sits in is the truth.
                project.folder = str(folder)
            yield project

    def _index(self, project: Project) -> None:
        """Upsert the index row. The index is only a cache, so a database that was deleted
        or replaced while the app runs is simply recreated; it never blocks a save."""
        with LOCK:
            try:
                self._index_row(project)
            except sqlite3.OperationalError:
                self._ensure_table(force=True)
                self._index_row(project)

    def _folder_from_index(self, project_id: str) -> Path | None:
        with LOCK:
            try:
                with db.connect(self.app_data_dir) as conn:
                    row = conn.execute(
                        "SELECT folder FROM projects WHERE id = ?", (project_id,)
                    ).fetchone()
            except sqlite3.OperationalError:
                self._ensure_table(force=True)
                return None
        return Path(row["folder"]) if row else None
