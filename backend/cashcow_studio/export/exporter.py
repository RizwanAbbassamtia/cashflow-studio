"""The export folder (docs/M3-M4-CONTRACT.md section 4): where the finished files go and
how they are named.

Layout::

    <channel export_folder or settings exports_dir>/<channel-slug>/<date>_<topic-slug>/
        <topic-slug>_1080p.mp4   (one per rendered preset)
        <topic-slug>_thumbnail.png          the chosen 16:9 thumbnail (1280x720)
        <topic-slug>_thumbnail_shorts.png   the chosen 9:16 thumbnail (1080x1920)
        metadata.json  provenance.json  provenance.md

``<date>`` is the project's creation date (the same stamp as the project folder), so a redo
lands in the same place and overwrites the files instead of making a second folder. A channel
export folder that cannot be used (a drive that is not plugged in) falls back to the app's
exports folder with a warning; the files are never lost.

The copies happen when the export stage is approved (or on the run itself when the stage is
set to auto); until then the pack stays inside ``08_export``. A copy whose destination
already holds the same file (same size and modification time) is skipped, so re-approving
after a metadata edit does not copy a multi-gigabyte video again.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from ..models.channel import Channel
from ..models.export import ExportedFile, ThumbnailVariant
from ..models.project import Project

log = logging.getLogger(__name__)

DEFAULT_PRESETS = ["1080p"]
KNOWN_PRESETS = ("720p", "1080p", "2160p")
METADATA_FILE = "metadata.json"
PROVENANCE_JSON = "provenance.json"
PROVENANCE_MD = "provenance.md"
EXPORT_FILE = "export.json"
FINAL_PREFIX = "final_"


def export_base(channel: Channel, settings: Any) -> tuple[Path, str | None]:
    """The channel's export folder when it can be used, else the app's exports folder."""
    configured = (channel.channel.export_folder or "").strip().strip('"')
    fallback = Path(getattr(settings, "exports_dir", Path.cwd() / "exports"))
    if not configured:
        return fallback, None
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        return fallback, (
            f"The channel's export folder '{configured}' is not a full path; the files went "
            f"to {fallback} instead."
        )
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return fallback, (
            f"The channel's export folder {candidate} could not be used ({exc}); the files "
            f"went to {fallback} instead."
        )
    return candidate, None


def export_folder_for(channel: Channel, settings: Any, project: Project) -> tuple[Path, list[str]]:
    """``<base>/<channel-slug>/<date>_<topic-slug>`` (the slug is not repeated when the
    base already ends with it)."""
    warnings: list[str] = []
    base, warning = export_base(channel, settings)
    if warning:
        warnings.append(warning)
    if base.name.lower() != project.channel_slug.lower():
        base = base / project.channel_slug
    stamp = project.created_at.strftime("%Y-%m-%d")
    folder = base / f"{stamp}_{project.topic_slug}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fallback = Path(getattr(settings, "exports_dir", base)) / project.channel_slug / folder.name
        warnings.append(
            f"The export folder {folder} could not be created ({exc}); using {fallback}."
        )
        fallback.mkdir(parents=True, exist_ok=True)
        folder = fallback
    return folder, warnings


def final_path(project_folder: Path, preset: str) -> Path:
    return Path(project_folder) / "07_edit" / f"{FINAL_PREFIX}{preset}.mp4"


def rendered_presets(project_folder: Path) -> list[str]:
    edit_dir = Path(project_folder) / "07_edit"
    if not edit_dir.is_dir():
        return []
    found = []
    for path in sorted(edit_dir.glob(f"{FINAL_PREFIX}*.mp4")):
        preset = path.stem[len(FINAL_PREFIX):]
        if preset:
            found.append(preset)
    return found


def selected_presets(
    project_folder: Path, settings: Any, timeline: dict[str, Any] | None = None
) -> list[str]:
    """The presets the person asked for: the timeline's list, else Settings > Render, else
    1080p. Presets that were rendered but not asked for are exported as well."""
    chosen: list[str] = []
    raw = (timeline or {}).get("presets")
    if isinstance(raw, list):
        chosen = [str(p).strip() for p in raw if str(p).strip()]
    if not chosen:
        render = getattr(settings, "render", None)
        defaults = getattr(render, "default_presets", None)
        if isinstance(defaults, list) and defaults:
            chosen = [str(p).strip() for p in defaults if str(p).strip()]
    if not chosen:
        chosen = list(DEFAULT_PRESETS)
    for preset in rendered_presets(project_folder):
        if preset not in chosen:
            chosen.append(preset)
    return chosen


def video_name(topic_slug: str, preset: str) -> str:
    return f"{topic_slug}_{preset}.mp4"


def thumbnail_names(topic_slug: str) -> tuple[str, str]:
    return f"{topic_slug}_thumbnail.png", f"{topic_slug}_thumbnail_shorts.png"


def same_file_already_there(source: Path, destination: Path) -> bool:
    """True when ``destination`` is a finished copy of ``source`` (``copy2`` keeps the
    modification time, so size and mtime together identify it)."""
    try:
        src, dst = source.stat(), destination.stat()
    except OSError:
        return False
    return src.st_size == dst.st_size and abs(src.st_mtime - dst.st_mtime) < 1.0


def copy_file(source: Path, destination: Path, project_folder: Path, kind: str) -> ExportedFile:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not same_file_already_there(source, destination):
        shutil.copy2(source, destination)
    try:
        relative = Path(source).resolve().relative_to(Path(project_folder).resolve()).as_posix()
    except ValueError:
        relative = str(source)
    return ExportedFile(
        name=destination.name, source=relative, bytes=destination.stat().st_size, kind=kind,  # type: ignore[arg-type]
    )


def locate_videos(
    project_folder: Path, topic_slug: str, presets: list[str]
) -> tuple[dict[str, Path], list[str], dict[str, str]]:
    """Which rendered presets exist: ``(preset -> final file, missing presets, preset ->
    export name)``. Nothing is copied."""
    found: dict[str, Path] = {}
    missing: list[str] = []
    names: dict[str, str] = {}
    for preset in presets:
        source = final_path(project_folder, preset)
        if not source.is_file() or source.stat().st_size == 0:
            missing.append(preset)
            continue
        found[preset] = source
        names[preset] = video_name(topic_slug, preset)
    return found, missing, names


def copy_videos(
    project_folder: Path, export_dir: Path, topic_slug: str, presets: list[str]
) -> tuple[list[ExportedFile], list[str], dict[str, str]]:
    """Copy every rendered preset; report the ones that are missing."""
    found, missing, names = locate_videos(project_folder, topic_slug, presets)
    files = [
        copy_file(source, export_dir / names[preset], project_folder, "video")
        for preset, source in found.items()
    ]
    return files, missing, names


def copy_thumbnails(
    export_stage_dir: Path, export_dir: Path, topic_slug: str, variant: ThumbnailVariant | None,
    project_folder: Path,
) -> list[ExportedFile]:
    if variant is None:
        return []
    names = thumbnail_names(topic_slug)
    files: list[ExportedFile] = []
    for source_name, target_name in zip((variant.file, variant.file_shorts), names, strict=True):
        source = export_stage_dir / source_name
        if source.is_file():
            files.append(copy_file(source, export_dir / target_name, project_folder, "thumbnail"))
    return files


def copy_documents(
    export_stage_dir: Path, export_dir: Path, project_folder: Path
) -> list[ExportedFile]:
    files: list[ExportedFile] = []
    for name, kind in ((METADATA_FILE, "metadata"), (PROVENANCE_JSON, "provenance"),
                       (PROVENANCE_MD, "provenance")):
        source = export_stage_dir / name
        if source.is_file():
            files.append(copy_file(source, export_dir / name, project_folder, kind))
    return files
