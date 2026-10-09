"""Project files over HTTP (docs/M3-M4-CONTRACT.md section 5).

* ``GET /api/projects/{id}/files/{path}`` serves one file from inside the project folder:
  the path is checked segment by segment (no ``..``, no absolute paths, no drive letters)
  and resolved; anything that lands outside the folder, or does not exist, is a 404. The
  content type follows the extension (wav, mp4, png, jpg, json ...), ``Accept-Ranges`` is
  advertised and ``Range`` requests are honoured (206 / 416) so the browser can seek in
  audio and video.
* ``POST /api/projects/{id}/upload/{stage}`` (multipart ``file``, optional ``scene``) saves
  a file into ``<stage dir>/uploads/`` under a safe name and returns its path relative to
  the project folder, for "upload own recording" and "upload image".
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PureWindowsPath
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from slugify import slugify

from ..models.project import StageName
from ..storage.project_store import STAGE_DIRS, ProjectNotFound, ProjectStoreError
from .projects import EngineDep

router = APIRouter(prefix="/api/projects", tags=["files"])

CONTENT_TYPES: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".ass": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".vtt": "text/vtt; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"

UploadStage = Literal["voice", "images", "edit", "export"]
UPLOAD_STAGES: dict[str, StageName] = {
    "voice": StageName.voice,
    "images": StageName.images,
    "edit": StageName.edit,
    "export": StageName.export,
}
UPLOADS_DIR = "uploads"
ALLOWED_UPLOAD_EXTENSIONS: dict[str, frozenset[str]] = {
    "voice": frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
                        ".mp4", ".webm", ".mov", ".mkv"}),
    "images": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "edit": frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4",
                       ".webm", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".webp", ".json",
                       ".ass", ".srt"}),
    "export": frozenset({".png", ".jpg", ".jpeg", ".webp", ".mp4", ".json", ".txt", ".md"}),
}
MAX_NAME_LENGTH = 60
CHUNK_SIZE = 1024 * 1024
NOT_FOUND = "That file is not part of this project."


class UploadResult(BaseModel):
    path: str
    """Relative to the project folder, with forward slashes (e.g. 05_voice/uploads/take1.wav)."""
    stage: UploadStage
    name: str
    size_bytes: int
    scene: int | None = None


# Path safety --------------------------------------------------------------------------------


def _segments(relative: str) -> list[str]:
    """The path's segments, or an empty list when the path is not a plain relative path."""
    text = (relative or "").replace("\\", "/")
    if not text or "\x00" in text or text.startswith("/"):
        return []
    if PureWindowsPath(text).drive or PureWindowsPath(text).is_absolute():
        return []
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts:
        return []
    for part in parts:
        if part == ".." or part.endswith(":") or ":" in part:
            return []
    return parts


def resolve_project_file(folder: Path, relative: str) -> Path:
    """The file ``relative`` names inside ``folder``; 404 for traversal, outside or missing."""
    parts = _segments(relative)
    if not parts:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        root = Path(folder).resolve()
        target = root.joinpath(*parts).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return target


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_CONTENT_TYPE)


async def _project_folder(engine: EngineDep, project_id: str) -> Path:
    try:
        project = await asyncio.to_thread(engine.get, project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Path(project.folder)


# Serving ------------------------------------------------------------------------------------


@router.api_route(
    "/{project_id}/files/{path:path}", methods=["GET", "HEAD"], response_class=FileResponse
)
async def get_project_file(
    project_id: str, path: str, request: Request, engine: EngineDep
) -> FileResponse:
    """One file from the project folder, with ``Range`` support for seeking."""
    folder = await _project_folder(engine, project_id)
    target = resolve_project_file(folder, path)
    # Starlette's FileResponse answers Range requests itself (206 with Content-Range, 416
    # when the range is unsatisfiable) and HEAD requests with the headers only.
    return FileResponse(
        target,
        media_type=content_type_for(target),
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )


# Upload -------------------------------------------------------------------------------------


def safe_upload_name(filename: str | None, stage: str, scene: int | None) -> str:
    """``scene_03_my-take.wav``: a slug of the stem, the original extension (lower-cased)."""
    original = Path((filename or "").replace("\\", "/")).name
    suffix = Path(original).suffix.lower()
    stem = Path(original).stem
    if not suffix and original.startswith(".") and original.count(".") == 1:
        suffix, stem = original.lower(), ""  # a bare ".wav": no name, only a type
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS[stage]:
        allowed = ", ".join(sorted(e.lstrip(".") for e in ALLOWED_UPLOAD_EXTENSIONS[stage]))
        raise HTTPException(
            status_code=415,
            detail=f"That file type is not supported for the {stage} step. Use: {allowed}.",
        )
    clean = slugify(stem, max_length=MAX_NAME_LENGTH, word_boundary=True) or "upload"
    prefix = f"scene_{scene:02d}_" if scene is not None else ""
    return f"{prefix}{clean}{suffix}"


def unique_path(folder: Path, name: str) -> Path:
    candidate = folder / name
    counter = 2
    stem, suffix = Path(name).stem, Path(name).suffix
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _save_upload(upload: UploadFile, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with tmp.open("wb") as handle:
            upload.file.seek(0)
            while True:
                chunk = upload.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                size += len(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="The uploaded file is empty.")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return size


@router.post("/{project_id}/upload/{stage}", response_model=UploadResult)
async def upload_project_file(
    project_id: str,
    stage: str,
    engine: EngineDep,
    file: Annotated[UploadFile, File()],
    scene: Annotated[int | None, Form()] = None,
) -> UploadResult:
    """Save a file into ``<stage dir>/uploads/`` and return its project-relative path."""
    key = stage.strip().lower()
    if key not in UPLOAD_STAGES:
        raise HTTPException(
            status_code=404,
            detail="Uploads go to the voice, images, edit or export step.",
        )
    if scene is not None and scene < 0:
        raise HTTPException(status_code=422, detail="The scene number cannot be negative.")
    folder = await _project_folder(engine, project_id)
    if not folder.is_dir():
        raise HTTPException(
            status_code=404, detail=f"The project folder {folder} is missing on this PC."
        )
    name = safe_upload_name(file.filename, key, scene)
    stage_dir = folder / STAGE_DIRS[UPLOAD_STAGES[key]] / UPLOADS_DIR
    dest = unique_path(stage_dir, name)
    try:
        size = await asyncio.to_thread(_save_upload, file, dest)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The file could not be saved: {exc}"
        ) from exc
    finally:
        await file.close()
    relative = dest.relative_to(folder).as_posix()
    return UploadResult(path=relative, stage=key, name=dest.name, size_bytes=size, scene=scene)


__all__ = ["router", "resolve_project_file", "safe_upload_name"]
