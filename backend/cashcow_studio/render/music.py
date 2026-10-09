"""Background music from the channel's music folder.

The pick is deterministic per project (a hash of the project id chooses the track), so a redo
keeps the same music unless the reviewer picks another. A track counts as licensed when a
``<track>.license.txt`` sidecar sits next to it or the folder holds a ``LICENSE*`` file; the
``edit.music_license`` gate warns otherwise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models.timeline import MusicTrackInfo

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}
)
LICENCE_PREFIXES = ("license", "licence")


def list_tracks(folder: Path | str | None) -> list[Path]:
    """Audio files directly inside the folder, sorted by name; empty when it is unusable."""
    if not folder:
        return []
    root = Path(folder)
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [p for p in entries if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]


def folder_has_licence(folder: Path) -> bool:
    try:
        return any(
            p.is_file() and p.name.lower().startswith(LICENCE_PREFIXES) for p in folder.iterdir()
        )
    except OSError:
        return False


def has_licence(track: Path | str) -> bool:
    """``<track>.license.txt`` (or ``.licence.txt``, with or without the audio extension) next
    to the file, or a ``LICENSE*`` file anywhere in the folder."""
    path = Path(track)
    folder = path.parent
    for base in (path.name, path.stem):
        for word in LICENCE_PREFIXES:
            if (folder / f"{base}.{word}.txt").is_file():
                return True
    return folder_has_licence(folder)


def pick_track(folder: Path | str | None, project_id: str) -> Path | None:
    """One track from the folder, the same one every time for this project."""
    tracks = list_tracks(folder)
    if not tracks:
        return None
    digest = hashlib.sha256((project_id or "").encode("utf-8")).hexdigest()
    return tracks[int(digest[:8], 16) % len(tracks)]


def track_infos(folder: Path | str | None) -> list[MusicTrackInfo]:
    return [
        MusicTrackInfo(path=str(track), name=track.stem, license_ok=has_licence(track))
        for track in list_tracks(folder)
    ]


def resolve_track(folder: Path | str | None, chosen: str) -> Path | None:
    """The track the reviewer named: an absolute path, or a name inside the music folder."""
    text = (chosen or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_file():
        return candidate
    if folder:
        for track in list_tracks(folder):
            if track.name == text or track.stem == text or track.name == candidate.name:
                return track
    return None
