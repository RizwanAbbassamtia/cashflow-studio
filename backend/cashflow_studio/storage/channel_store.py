"""File-based channel storage: one folder per channel under ``<shared_dir>/channels``.

Layout::

    <shared_dir>/channels/<slug>/channel.json
    <shared_dir>/channels/<slug>/frameworks/<file>
    <shared_dir>/channels/_archived/<slug>-<UTC timestamp>/   (archived channels, never deleted)

Every check-then-write (does the slug exist? is the file name free?) runs under :data:`LOCK`,
because FastAPI serves these endpoints from a thread pool and two saves can overlap.
"""

from __future__ import annotations

import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ValidationError
from slugify import slugify

from ..models.channel import Channel, ChannelSummary, Framework, FrameworkType
from .settings_store import atomic_write_text

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
SLUG_RE = re.compile(SLUG_PATTERN)
CHANNEL_FILE = "channel.json"
ARCHIVE_DIR = "_archived"
FRAMEWORKS_DIR = "frameworks"
# Keeps <shared_dir>/channels/<80-char slug>/frameworks/<stem>-10.<ext> under the 260-character
# Windows path limit even with the default shared folder under %LOCALAPPDATA%.
MAX_FILENAME_LENGTH = 80
MAX_SUFFIX_LENGTH = 16
# Windows device names: a file cannot be called CON, NUL.txt, com1.pdf and so on.
WINDOWS_RESERVED_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)

LOCK = threading.RLock()


class ChannelStoreError(Exception):
    """Base class; the message is plain English and safe to show to the user."""


class ChannelNotFound(ChannelStoreError):
    pass


class ChannelExists(ChannelStoreError):
    pass


class InvalidSlug(ChannelStoreError):
    pass


class InvalidChannelName(ChannelStoreError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def make_slug(name: str) -> str:
    """Folder name derived from the channel name, e.g. ``Kind Ledger`` -> ``kind-ledger``."""
    slug = slugify(name or "", max_length=80, word_boundary=True)
    if not SLUG_RE.match(slug):
        raise InvalidChannelName(
            "The channel name must contain at least one letter or digit so a folder name can "
            "be made from it."
        )
    return slug


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise InvalidSlug(
            f"'{slug}' is not a valid channel id. Use lowercase letters, digits and single "
            "hyphens, for example kind-ledger."
        )
    return slug


def safe_filename(filename: str | None, fallback: str = "framework") -> str:
    """Keep only letters, digits, dots, hyphens and underscores; drop any folder parts."""
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_{2,}", "_", name).strip("._-")
    if not name:
        name = fallback
    stem, dot, suffix = name.rpartition(".")
    if not dot or len(suffix) > MAX_SUFFIX_LENGTH:
        stem, suffix = name, ""  # no extension, or far too long to be one
    if not stem:
        stem = fallback
    if len(stem) > MAX_FILENAME_LENGTH:
        stem = stem[:MAX_FILENAME_LENGTH].rstrip("._-") or fallback
    if WINDOWS_RESERVED_RE.match(stem.split(".", 1)[0]):
        stem = f"_{stem}"  # Windows judges the part before the first dot, so prefix it
    return f"{stem}.{suffix}" if suffix else stem


def display_name(filename: str | None, fallback: str) -> str:
    """Human-friendly name for a framework: the original file name without its extension."""
    base = (filename or "").replace("\\", "/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    clean = "".join(ch for ch in stem if ch.isprintable()).strip()
    return clean[:120] or fallback


def summary_of(channel: Channel) -> ChannelSummary:
    return ChannelSummary(
        slug=channel.slug,
        name=channel.channel.name,
        language=channel.channel.language,
        formats=channel.channel.formats,
        status=channel.channel.status,
        competitors=len(channel.competitors),
        updated_at=channel.updated_at,
    )


class ChannelStore:
    """Reads and writes ``channel.json`` files under the shared folder."""

    def __init__(self, shared_dir: Path | str) -> None:
        self.shared_dir = Path(shared_dir)
        self.channels_dir = self.shared_dir / "channels"

    # Paths ------------------------------------------------------------------------------

    def channel_dir(self, slug: str) -> Path:
        return self.channels_dir / check_slug(slug)

    def channel_file(self, slug: str) -> Path:
        return self.channel_dir(slug) / CHANNEL_FILE

    def exists(self, slug: str) -> bool:
        return self.channel_file(slug).is_file()

    # CRUD -------------------------------------------------------------------------------

    def list(self) -> list[ChannelSummary]:
        """Every readable channel, sorted by name. Broken files are skipped, not fatal."""
        if not self.channels_dir.is_dir():
            return []
        rows: list[ChannelSummary] = []
        for folder in self.channels_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            path = folder / CHANNEL_FILE
            if not path.is_file():
                continue
            try:
                channel = Channel.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError):
                continue
            rows.append(summary_of(channel))
        rows.sort(key=lambda row: (row.name.casefold(), row.slug))
        return rows

    def get(self, slug: str) -> Channel:
        path = self.channel_file(slug)
        if not path.is_file():
            raise ChannelNotFound(f"There is no channel called '{slug}'.")
        try:
            return Channel.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as exc:
            raise ChannelStoreError(
                f"The file for channel '{slug}' could not be read: {exc}"
            ) from exc

    def create(self, channel: Channel) -> Channel:
        """Save a new channel. The slug must already be set; timestamps are set here."""
        check_slug(channel.slug)
        with LOCK:
            if self.exists(channel.slug):
                raise ChannelExists(
                    f"A channel with the id '{channel.slug}' already exists. "
                    "Choose a different name."
                )
            now = utc_now()
            saved = channel.model_copy(update={"created_at": now, "updated_at": now})
            self._write(saved)
            return saved

    def update(self, slug: str, channel: Channel) -> Channel:
        """Replace an existing channel; keeps ``created_at`` and sets ``updated_at``."""
        with LOCK:
            existing = self.get(slug)
            saved = channel.model_copy(
                update={
                    "slug": slug,
                    "created_at": existing.created_at or utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self._write(saved)
            return saved

    def archive(self, slug: str) -> Path:
        """Move the channel folder to ``_archived/<slug>-<UTC timestamp>``. Never deletes."""
        folder = self.channel_dir(slug)
        with LOCK:
            if not (folder / CHANNEL_FILE).is_file():
                raise ChannelNotFound(f"There is no channel called '{slug}'.")
            archive_root = self.channels_dir / ARCHIVE_DIR
            archive_root.mkdir(parents=True, exist_ok=True)
            stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
            destination = archive_root / f"{slug}-{stamp}"
            counter = 1
            while destination.exists():
                destination = archive_root / f"{slug}-{stamp}-{counter}"
                counter += 1
            shutil.move(str(folder), str(destination))
            return destination

    # Frameworks -------------------------------------------------------------------------

    def save_framework(
        self, slug: str, filename: str | None, data: bytes, framework_type: FrameworkType
    ) -> Framework:
        """Store an uploaded framework file and return its entry for the channel's list.

        The returned ``path`` is relative to the shared folder. The channel file itself is
        not changed: the Channel Setup form adds the entry and saves it with the channel.
        """
        folder = self.channel_dir(slug)
        safe = safe_filename(filename)
        with LOCK:
            if not (folder / CHANNEL_FILE).is_file():
                raise ChannelNotFound(f"There is no channel called '{slug}'.")
            target_dir = folder / FRAMEWORKS_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_path(target_dir / safe)
            if target_dir.resolve() not in target.resolve().parents:
                raise InvalidSlug("That file name cannot be used.")
            target.write_bytes(data)
        return Framework(
            type=framework_type,
            name=display_name(filename, fallback=Path(safe).stem),
            path=target.relative_to(self.shared_dir).as_posix(),
        )

    # Internals --------------------------------------------------------------------------

    def _write(self, channel: Channel) -> None:
        folder = self.channel_dir(channel.slug)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / CHANNEL_FILE, channel.model_dump_json(indent=2) + "\n")


def _unique_path(path: Path) -> Path:
    """``name.ext`` -> ``name-2.ext``, ``name-3.ext`` ... if a file already exists."""
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


# YouTube URL check (syntactic only, no network) ------------------------------------------

UrlKind = Literal["channel", "video", "unknown"]

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
LEGACY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
VIDEO_PREFIXES = {"shorts", "embed", "live", "v"}


class UrlCheck(BaseModel):
    ok: bool
    kind: UrlKind
    normalized: str


def validate_url(url: str) -> UrlCheck:
    """Classify a YouTube link as a channel or video link and return its canonical form."""
    raw = (url or "").strip()
    unknown = UrlCheck(ok=False, kind="unknown", normalized=raw)
    if not raw:
        return unknown
    candidate = raw if SCHEME_RE.match(raw) else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return unknown
    if parts.scheme not in {"http", "https"} or not host:
        return unknown
    segments = [segment for segment in parts.path.split("/") if segment]

    if host == "youtu.be":
        if segments and VIDEO_ID_RE.match(segments[0]):
            return _video(segments[0])
        return unknown
    if host not in YOUTUBE_HOSTS or not segments:
        return unknown

    head = segments[0]
    if head == "watch":
        video_id = (parse_qs(parts.query).get("v") or [""])[0]
        return _video(video_id) if VIDEO_ID_RE.match(video_id) else unknown
    if head in VIDEO_PREFIXES and len(segments) >= 2 and VIDEO_ID_RE.match(segments[1]):
        return _video(segments[1])
    if head == "channel" and len(segments) >= 2 and CHANNEL_ID_RE.match(segments[1]):
        return _channel(f"https://www.youtube.com/channel/{segments[1]}")
    if HANDLE_RE.match(head):
        return _channel(f"https://www.youtube.com/{head}")
    if head in {"c", "user"} and len(segments) >= 2 and LEGACY_NAME_RE.match(segments[1]):
        return _channel(f"https://www.youtube.com/{head}/{segments[1]}")
    return unknown


def _video(video_id: str) -> UrlCheck:
    return UrlCheck(
        ok=True, kind="video", normalized=f"https://www.youtube.com/watch?v={video_id}"
    )


def _channel(normalized: str) -> UrlCheck:
    return UrlCheck(ok=True, kind="channel", normalized=normalized)
