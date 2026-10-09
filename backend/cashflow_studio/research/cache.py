"""SQLite cache for the research engine (tables in ``<app_data_dir>/db.sqlite``).

* ``research_videos``: every flat entry (and, once fetched, the exact details) keyed by
  video id with ``fetched_at``. Rows belong to one ``(channel_url, tab)`` listing.
* ``research_scans``: one row per ``(channel_url, tab)``: when it was listed, what the
  channel is called, how many videos were found, or the error. A tab is not listed again
  within ``cache_minutes`` unless the scan is forced (see ``scanner.py``).
* ``research_picks``: which competitor video each channel already started a project from,
  so the same video is never picked twice.

Tables are created idempotently with ``storage.db.ensure_table``. ``title_history`` belongs
to the pipeline; it is only read here, and only if it exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..models.research import FlatVideo, Tab, TabListing, VideoDetails
from ..storage.db import connect, ensure_table

CREATE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS research_videos (
        video_id TEXT PRIMARY KEY,
        channel_url TEXT NOT NULL,
        channel_id TEXT,
        tab TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        flat_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        details_json TEXT,
        details_fetched_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_research_videos_listing
        ON research_videos (channel_url, tab, fetched_at, position)
    """,
    """
    CREATE TABLE IF NOT EXISTS research_scans (
        channel_url TEXT NOT NULL,
        tab TEXT NOT NULL,
        channel_id TEXT,
        channel_name TEXT NOT NULL DEFAULT '',
        channel_follower_count INTEGER,
        scanned_at TEXT NOT NULL,
        videos_found INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        PRIMARY KEY (channel_url, tab)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_picks (
        channel_slug TEXT NOT NULL,
        video_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        picked_at TEXT NOT NULL,
        PRIMARY KEY (channel_slug, video_id, project_id)
    )
    """,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ScanRecord:
    channel_url: str
    tab: Tab
    channel_id: str | None
    channel_name: str
    channel_follower_count: int | None
    scanned_at: datetime
    videos_found: int
    error: str | None

    def is_fresh(self, max_age_minutes: int, now: datetime | None = None) -> bool:
        if self.error:
            return False
        now = now or utc_now()
        return now - self.scanned_at < timedelta(minutes=max_age_minutes)


class ResearchCache:
    def __init__(self, app_data_dir: Path | str) -> None:
        self.app_data_dir = Path(app_data_dir)
        self._ready = False

    def ensure_tables(self) -> None:
        if self._ready:
            return
        for statement in CREATE_STATEMENTS:
            ensure_table(self.app_data_dir, statement)
        self._ready = True

    # Scans ---------------------------------------------------------------------------------

    def get_scan(self, channel_url: str, tab: Tab) -> ScanRecord | None:
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            row = conn.execute(
                "SELECT * FROM research_scans WHERE channel_url = ? AND tab = ?",
                (channel_url, tab),
            ).fetchone()
        return _scan_from_row(row) if row else None

    def save_listing(self, listing: TabListing) -> None:
        """Store a fresh listing: upsert its videos (details are kept) and the scan row."""
        self.ensure_tables()
        stamp = _iso(listing.scanned_at)
        with connect(self.app_data_dir) as conn:
            for video in listing.videos:
                conn.execute(
                    """
                    INSERT INTO research_videos
                        (video_id, channel_url, channel_id, tab, position, flat_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        channel_url = excluded.channel_url,
                        channel_id = excluded.channel_id,
                        tab = excluded.tab,
                        position = excluded.position,
                        flat_json = excluded.flat_json,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        video.video_id,
                        listing.channel_url,
                        video.channel_id or listing.channel_id,
                        listing.tab,
                        video.position,
                        video.model_dump_json(),
                        stamp,
                    ),
                )
            conn.execute(
                """
                INSERT INTO research_scans
                    (channel_url, tab, channel_id, channel_name, channel_follower_count,
                     scanned_at, videos_found, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(channel_url, tab) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    channel_name = excluded.channel_name,
                    channel_follower_count = excluded.channel_follower_count,
                    scanned_at = excluded.scanned_at,
                    videos_found = excluded.videos_found,
                    error = NULL
                """,
                (
                    listing.channel_url,
                    listing.tab,
                    listing.channel_id,
                    listing.channel_name,
                    listing.channel_follower_count,
                    stamp,
                    len(listing.videos),
                ),
            )

    def save_scan_error(
        self, channel_url: str, tab: Tab, error: str, now: datetime | None = None
    ) -> None:
        """Remember that listing failed; an earlier good listing's videos stay available."""
        self.ensure_tables()
        stamp = _iso(now or utc_now())
        with connect(self.app_data_dir) as conn:
            existing = conn.execute(
                "SELECT scanned_at FROM research_scans WHERE channel_url = ? AND tab = ?",
                (channel_url, tab),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE research_scans SET error = ? WHERE channel_url = ? AND tab = ?",
                    (error, channel_url, tab),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO research_scans
                        (channel_url, tab, channel_name, scanned_at, videos_found, error)
                    VALUES (?, ?, '', ?, 0, ?)
                    """,
                    (channel_url, tab, stamp, error),
                )

    def load_listing(self, channel_url: str, tab: Tab) -> TabListing | None:
        """The last good listing of a tab from the cache, or ``None``."""
        self.ensure_tables()
        scan = self.get_scan(channel_url, tab)
        if scan is None or (scan.videos_found == 0 and scan.error):
            return None
        with connect(self.app_data_dir) as conn:
            rows = conn.execute(
                """
                SELECT flat_json FROM research_videos
                WHERE channel_url = ? AND tab = ? AND fetched_at = ?
                ORDER BY position
                """,
                (channel_url, tab, _iso(scan.scanned_at)),
            ).fetchall()
        videos = [FlatVideo.model_validate_json(row["flat_json"]) for row in rows]
        return TabListing(
            channel_url=channel_url,
            tab=tab,
            channel_id=scan.channel_id,
            channel_name=scan.channel_name,
            channel_follower_count=scan.channel_follower_count,
            scanned_at=scan.scanned_at,
            videos=videos,
            from_cache=True,
        )

    # Details -------------------------------------------------------------------------------

    def get_details(
        self, video_id: str, *, max_age_minutes: int | None = None, now: datetime | None = None
    ) -> VideoDetails | None:
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            row = conn.execute(
                "SELECT details_json, details_fetched_at FROM research_videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        if not row or not row["details_json"]:
            return None
        if max_age_minutes is not None:
            fetched = _parse(row["details_fetched_at"])
            age = (now or utc_now()) - fetched if fetched is not None else None
            if age is None or age >= timedelta(minutes=max_age_minutes):
                return None
        return VideoDetails.model_validate_json(row["details_json"])

    def save_details(self, details: VideoDetails) -> None:
        self.ensure_tables()
        stamp = _iso(details.fetched_at)
        with connect(self.app_data_dir) as conn:
            updated = conn.execute(
                """
                UPDATE research_videos SET details_json = ?, details_fetched_at = ?
                WHERE video_id = ?
                """,
                (details.model_dump_json(), stamp, details.video_id),
            ).rowcount
            if not updated:
                placeholder = FlatVideo(
                    video_id=details.video_id,
                    url=details.url,
                    title=details.title,
                    channel_url=details.channel_url or "",
                    channel_id=details.channel_id,
                    channel_name=details.channel_name,
                    tab="videos",
                    format="long",
                    position=0,
                    duration_s=details.duration_s,
                    view_count=details.view_count,
                    published_at=details.published_at,
                    thumbnail_url=details.thumbnail_url,
                    live_status=details.live_status,
                    channel_follower_count=details.channel_follower_count,
                    fetched_at=details.fetched_at,
                )
                conn.execute(
                    """
                    INSERT INTO research_videos
                        (video_id, channel_url, channel_id, tab, position, flat_json,
                         fetched_at, details_json, details_fetched_at)
                    VALUES (?, ?, ?, 'videos', 0, ?, ?, ?, ?)
                    """,
                    (
                        details.video_id,
                        details.channel_url or "",
                        details.channel_id,
                        placeholder.model_dump_json(),
                        stamp,
                        details.model_dump_json(),
                        stamp,
                    ),
                )

    # Picks and history ---------------------------------------------------------------------

    def record_pick(
        self, channel_slug: str, video_id: str, project_id: str, picked_at: datetime | None = None
    ) -> None:
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_picks
                    (channel_slug, video_id, project_id, picked_at)
                VALUES (?, ?, ?, ?)
                """,
                (channel_slug, video_id, project_id, _iso(picked_at or utc_now())),
            )

    def replace_pick(
        self, channel_slug: str, video_id: str, project_id: str, picked_at: datetime | None = None
    ) -> None:
        """The project's one current pick: earlier picks of the same project are forgotten.

        A pick a person overrode, or one replaced by a redo, must not count as "already used
        by this channel": no video was made from it.
        """
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            conn.execute(
                "DELETE FROM research_picks WHERE channel_slug = ? AND project_id = ?",
                (channel_slug, project_id),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO research_picks
                    (channel_slug, video_id, project_id, picked_at)
                VALUES (?, ?, ?, ?)
                """,
                (channel_slug, video_id, project_id, _iso(picked_at or utc_now())),
            )

    def forget_project(self, channel_slug: str, project_id: str) -> int:
        """Drop a project's picks (it was archived); returns how many rows went."""
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            return conn.execute(
                "DELETE FROM research_picks WHERE channel_slug = ? AND project_id = ?",
                (channel_slug, project_id),
            ).rowcount

    def used_video_ids(
        self, channel_slug: str, *, exclude_project_id: str | None = None
    ) -> set[str]:
        self.ensure_tables()
        with connect(self.app_data_dir) as conn:
            rows = conn.execute(
                "SELECT video_id, project_id FROM research_picks WHERE channel_slug = ?",
                (channel_slug,),
            ).fetchall()
        return {
            row["video_id"]
            for row in rows
            if exclude_project_id is None or row["project_id"] != exclude_project_id
        }

    def used_titles(self, channel_slug: str) -> set[str]:
        """Case-folded titles from the pipeline's ``title_history`` table, if it exists."""
        self.ensure_tables()
        try:
            with connect(self.app_data_dir) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'title_history'"
                ).fetchone()
                if not exists:
                    return set()
                rows = conn.execute(
                    "SELECT title FROM title_history WHERE channel_slug = ?", (channel_slug,)
                ).fetchall()
        except sqlite3.Error:
            return set()
        return {str(row["title"]).casefold().strip() for row in rows if row["title"]}


def _scan_from_row(row: sqlite3.Row) -> ScanRecord:
    scanned = _parse(row["scanned_at"]) or utc_now()
    return ScanRecord(
        channel_url=row["channel_url"],
        tab=row["tab"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"] or "",
        channel_follower_count=row["channel_follower_count"],
        scanned_at=scanned,
        videos_found=int(row["videos_found"] or 0),
        error=row["error"],
    )
