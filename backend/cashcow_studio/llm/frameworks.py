"""The channel's framework files (title prompt, script framework, style guide) as plain text.

A :class:`~cashcow_studio.models.channel.Framework` names a file by a path **relative to the
shared folder** (that is what the upload endpoint stores). Only files inside the shared folder
are ever read: an absolute path, a ``..`` segment, a hidden file (``.env``) or a file that is
not a text or PDF document is refused and the built-in default framework is used instead, with
a note for the reviewer. The text of a framework is sent to the writing model, so this rule is
what keeps a hand-edited ``channel.json`` from shipping other files off the laptop.

PDFs are read with pypdf, ``.txt`` and ``.md`` directly. The extracted text is cached under
``<app_data_dir>/cache/frameworks/<hash>.txt`` (never next to the source, which may be a
synced team folder) and reused while it is newer than the source, so a 40-page PDF is parsed
once.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..models.channel import Channel, Framework, FrameworkType

log = logging.getLogger(__name__)

CACHE_DIR_NAME = "frameworks"
MAX_FRAMEWORK_CHARS = 120_000  # roughly 30k tokens; longer files are cut with a note
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {".pdf"}
LINK_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

OUTSIDE_MESSAGE = (
    "framework files must live inside the shared folder, as a path relative to it such as "
    "channels/<channel>/frameworks/title.txt"
)


class FrameworkError(Exception):
    """The framework file could not be read (plain-English message)."""


@dataclass
class FrameworkText:
    framework: Framework | None
    text: str
    source: str
    """``file`` when read from the channel's file, ``default`` for the built-in text."""
    note: str = ""
    """Plain-English note for the reviewer when something was not as expected."""

    @property
    def is_default(self) -> bool:
        return self.source == "default"


def is_link(raw: str) -> bool:
    """``https://docs.google.com/...``: a reference the app shows but never opens."""
    return bool(LINK_RE.match((raw or "").strip()))


def check_framework_path(raw: str) -> str | None:
    """Why a framework path is not acceptable, or ``None`` when it is fine.

    Accepts an empty path, a web link, or a relative path inside the shared folder whose file
    is a text or PDF document. Used by the channel API so the Channel Setup form shows the
    problem at once, and by :func:`resolve_path` before any file is read.
    """
    text = (raw or "").strip().strip('"')
    if not text or is_link(text):
        return None
    if PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute():
        return f"'{text}' is a full path; {OUTSIDE_MESSAGE}."
    if text.startswith(("\\\\", "//")) or (len(text) > 1 and text[1] == ":"):
        return f"'{text}' points outside the shared folder; {OUTSIDE_MESSAGE}."
    parts = [p for p in re.split(r"[\\/]+", text) if p]
    if any(part == ".." for part in parts):
        return f"'{text}' leaves the shared folder ('..'); {OUTSIDE_MESSAGE}."
    name = parts[-1] if parts else ""
    if name.startswith("."):
        return f"'{name}' is a hidden file and cannot be used as a framework."
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        return f"'{name}' is not a text or PDF file; frameworks must end in {allowed}."
    return None


def resolve_path(raw: str, shared_dir: Path) -> Path:
    """The file inside the shared folder, or :class:`FrameworkError` when it is not there."""
    problem = check_framework_path(raw)
    text = (raw or "").strip().strip('"')
    if problem is not None:
        raise FrameworkError(problem)
    if not text or is_link(text):
        raise FrameworkError(f"'{text}' is a link, not a file in the shared folder.")
    root = Path(shared_dir).resolve()
    path = (root / text).resolve()
    if root not in path.parents:
        raise FrameworkError(f"'{text}' points outside the shared folder.")
    return path


def select_framework(
    channel: Channel, types: tuple[FrameworkType, ...], fmt: str
) -> Framework | None:
    """First framework whose type is in ``types`` and whose formats fit ``fmt``.

    ``types`` is in order of preference; ``both`` matches every format.
    """
    for wanted in types:
        for framework in channel.frameworks:
            if framework.type != wanted:
                continue
            if framework.formats != "both" and framework.formats != fmt:
                continue
            if not framework.path.strip():
                continue
            return framework
    return None


def cache_path(path: Path, cache_dir: Path) -> Path:
    """``<cache_dir>/<sha256 of the resolved path>.txt``."""
    digest = hashlib.sha256(str(Path(path).resolve()).lower().encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest[:32]}.txt"


def extract_text(path: Path, cache_dir: Path | None = None) -> str:
    """Text of a PDF, TXT or MD file; cached under ``cache_dir`` when one is given."""
    path = Path(path)
    if not path.is_file():
        raise FrameworkError(f"The framework file {path} does not exist.")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise FrameworkError(f"{path.name} is not a text or PDF file.")
    cache = cache_path(path, cache_dir) if cache_dir is not None else None
    if cache is not None:
        try:
            if cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
                return cache.read_text(encoding="utf-8")
        except OSError:
            pass
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf_text(path)
        else:
            text = _plain_text(path)
    except FrameworkError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure becomes one plain message
        raise FrameworkError(f"The framework file {path.name} could not be read: {exc}") from exc
    text = clean_text(text)
    if not text:
        raise FrameworkError(
            f"No text could be read from {path.name}. If it is a scanned PDF, save it as a "
            "text file instead."
        )
    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
        except OSError as exc:  # a read-only app folder: fine, just no cache
            log.warning("Could not write the framework cache %s: %s", cache, exc)
    return text


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader  # imported here so the module loads without pypdf

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page should not lose the file
            log.warning("A page of %s could not be read: %s", path.name, exc)
    return "\n\n".join(pages)


def _plain_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def framework_cache_dir(app_data_dir: Path | None) -> Path | None:
    return Path(app_data_dir) / "cache" / CACHE_DIR_NAME if app_data_dir is not None else None


def framework_text(
    channel: Channel,
    types: tuple[FrameworkType, ...],
    fmt: str,
    shared_dir: Path,
    default_text: str,
    *,
    cache_dir: Path | None = None,
) -> FrameworkText:
    """The channel's framework of one of ``types`` as text, or the built-in default.

    Never raises: a missing, unreadable or out-of-bounds file falls back to ``default_text``
    with a note the stage shows to the reviewer.
    """
    framework = select_framework(channel, types, fmt)
    if framework is None:
        return FrameworkText(None, default_text.strip(), "default")
    try:
        path = resolve_path(framework.path, shared_dir)
        text = extract_text(path, cache_dir)
    except FrameworkError as exc:
        log.warning("Framework '%s' of channel %s unusable: %s", framework.name, channel.slug, exc)
        return FrameworkText(
            framework,
            default_text.strip(),
            "default",
            note=f"The framework '{framework.name}' could not be used ({exc}). "
            "The built-in default was used instead.",
        )
    note = ""
    if len(text) > MAX_FRAMEWORK_CHARS:
        text = text[:MAX_FRAMEWORK_CHARS]
        note = (
            f"The framework '{framework.name}' is very long; only the first "
            f"{MAX_FRAMEWORK_CHARS:,} characters were used."
        )
    return FrameworkText(framework, text, "file", note=note)
