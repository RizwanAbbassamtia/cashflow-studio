"""The style sheet: one picture every scene image is asked to resemble.

Order of preference (docs/M3-M4-CONTRACT.md section 2):

1. ``06_images/style_sheet.png`` already in the project (a person may have put it there);
2. the first picture in the channel's ``images.reference_folder`` (copied in as PNG);
3. nothing yet: the stage promotes the first accepted scene image.

Reference folders are given relative to the shared folder (like framework files) or as an
absolute path; a relative path may not climb out of the shared folder.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..models.images import StyleSheetSource

log = logging.getLogger(__name__)

STYLE_SHEET_FILE = "style_sheet.png"
DEFAULT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def reference_folder(setting: str, shared_dir: Path) -> Path | None:
    """The folder the channel points at, or ``None`` when the setting is blank or unusable."""
    text = (setting or "").strip()
    if not text:
        return None
    raw = Path(text).expanduser()
    if raw.is_absolute():
        return raw if raw.is_dir() else None
    base = Path(shared_dir).resolve()
    candidate = (base / raw).resolve()
    if not candidate.is_relative_to(base):
        log.warning("Reference folder %s points outside the shared folder; ignored.", text)
        return None
    return candidate if candidate.is_dir() else None


def list_reference_images(
    folder: Path | None, extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
) -> list[Path]:
    """Image files directly inside ``folder``, by name; hidden files and other types skipped."""
    if folder is None or not folder.is_dir():
        return []
    allowed = {e.lower() for e in extensions}
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in allowed
    )


def save_as_png(source: Path, target: Path, max_side: int = 2048) -> tuple[int, int]:
    """Copy ``source`` to ``target`` as PNG, shrunk so the long side is at most ``max_side``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        picture = image.convert("RGB")
    if max(picture.size) > max_side:
        picture.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    picture.save(target, "PNG")
    return picture.size


def ensure_style_sheet(
    stage_dir: Path, references: list[Path], max_side: int = 2048
) -> tuple[Path | None, StyleSheetSource]:
    """The style sheet to use now and where it came from (see the module notes)."""
    target = stage_dir / STYLE_SHEET_FILE
    if target.is_file():
        return target, "existing"
    for reference in references:
        try:
            save_as_png(reference, target, max_side)
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            log.warning("Reference picture %s could not be used: %s", reference, exc)
            continue
        return target, "channel_reference"
    return None, "none"


def promote_scene_image(stage_dir: Path, scene_path: Path, max_side: int = 2048) -> Path:
    """The first accepted scene picture becomes the style sheet for the scenes after it."""
    target = stage_dir / STYLE_SHEET_FILE
    save_as_png(scene_path, target, max_side)
    return target
