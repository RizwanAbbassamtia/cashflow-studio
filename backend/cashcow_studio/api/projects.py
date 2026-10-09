"""``/api/projects``: one project per video, moved through the stages by the engine.

See docs/M1-M2-CONTRACT.md section 1 for the table of endpoints. Every handler is ``async``
so it runs on the event loop that owns the engine's background tasks; the reads that only
touch files and SQLite (list, get, stage payload, create) run in a worker thread so a long
project list never freezes the WebSocket stream or the other requests.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ..models.channel import StageMode
from ..models.project import Project, ProjectCreate, ProjectSummary, StageName
from ..pipeline.engine import (
    EngineError,
    InvalidProjectRequest,
    InvalidTransition,
    PipelineEngine,
    ProjectBusy,
)
from ..storage.channel_store import ChannelNotFound, ChannelStoreError, InvalidSlug
from ..storage.project_store import ProjectNotFound, ProjectStoreError
from .deps import ChannelStoreDep

router = APIRouter(prefix="/api", tags=["projects"])


def get_engine(request: Request) -> PipelineEngine:
    return request.app.state.engine


EngineDep = Annotated[PipelineEngine, Depends(get_engine)]


class ReviewAction(BaseModel):
    """Body of approve / redo / skip.

    ``edits`` are stage specific (docs/M1-M2-CONTRACT.md section 1). On ``approve`` they are
    applied at once; on ``redo`` they are kept for the next run (the script stage reads
    ``locked_paragraph_ids`` from them, so the paragraphs locked in the editor survive a
    regenerate without an approval in between).
    """

    by: str = ""
    notes: str | None = None
    edits: dict[str, Any] | None = None


class ModeChange(BaseModel):
    mode: StageMode


class RunResponse(BaseModel):
    id: str
    current_stage: StageName
    status: str
    started: bool
    message: str


class OpenFolderResponse(BaseModel):
    ok: bool
    folder: str


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProjectBusy | InvalidTransition):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidProjectRequest):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, EngineError | ProjectStoreError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, OSError):
        return HTTPException(
            status_code=500,
            detail=f"The project files could not be written: {exc}. Check that the projects "
            "folder is available and not read-only.",
        )
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(
    body: ProjectCreate, engine: EngineDep, channels: ChannelStoreDep
) -> Project:
    try:
        channel = channels.get(body.channel_slug)
    except (ChannelNotFound, InvalidSlug) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        project = await asyncio.to_thread(engine.create_project, body, channel)
        await engine.run(project.id)
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc
    return await asyncio.to_thread(engine.get, project.id)


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects(
    engine: EngineDep,
    channel_slug: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> list[ProjectSummary]:
    try:
        return await asyncio.to_thread(engine.list, channel_slug or None, status or None)
    except (ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, engine: EngineDep) -> Project:
    try:
        return await asyncio.to_thread(engine.get, project_id)
    except (ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.get("/projects/{project_id}/stage/{stage}")
async def get_stage_payload(
    project_id: str, stage: StageName, engine: EngineDep
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(engine.stage_payload, project_id, stage)
    except (ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/stage/{stage}/approve", response_model=Project)
async def approve_stage(
    project_id: str, stage: StageName, body: ReviewAction, engine: EngineDep
) -> Project:
    try:
        return await engine.approve(
            project_id, stage, by=body.by, notes=body.notes, edits=body.edits
        )
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/stage/{stage}/redo", response_model=Project)
async def redo_stage(
    project_id: str, stage: StageName, body: ReviewAction, engine: EngineDep
) -> Project:
    try:
        return await engine.redo(
            project_id, stage, by=body.by, notes=body.notes, edits=body.edits
        )
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/stage/{stage}/skip", response_model=Project)
async def skip_stage(
    project_id: str, stage: StageName, body: ReviewAction, engine: EngineDep
) -> Project:
    try:
        return await engine.skip(project_id, stage, by=body.by, notes=body.notes)
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.put("/projects/{project_id}/stage/{stage}/mode", response_model=Project)
async def set_stage_mode(
    project_id: str, stage: StageName, body: ModeChange, engine: EngineDep
) -> Project:
    try:
        return await engine.set_mode(project_id, stage, body.mode)
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/run", response_model=RunResponse, status_code=202)
async def run_project(project_id: str, engine: EngineDep) -> RunResponse:
    try:
        project, started = await engine.run(project_id)
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc
    status = project.stages[project.current_stage].status
    if started:
        message = "Running."
    elif status == "awaiting_review":
        message = "Waiting for a review: approve, redo or skip this step to continue."
    elif status in ("done", "skipped"):
        message = "This project is already finished."
    else:
        message = "Already running."
    return RunResponse(
        id=project.id,
        current_stage=project.current_stage,
        status=status,
        started=started,
        message=message,
    )


@router.post("/projects/{project_id}/open-folder", response_model=OpenFolderResponse)
async def open_project_folder(project_id: str, engine: EngineDep) -> OpenFolderResponse:
    try:
        project = await asyncio.to_thread(engine.get, project_id)
    except (ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc
    folder = Path(project.folder)
    if not folder.is_dir():
        raise HTTPException(
            status_code=404, detail=f"The project folder {folder} is missing on this PC."
        )
    try:
        open_folder(folder)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The folder could not be opened: {exc}"
        ) from exc
    return OpenFolderResponse(ok=True, folder=str(folder))


@router.delete("/projects/{project_id}", status_code=204, response_class=Response)
async def archive_project(project_id: str, engine: EngineDep) -> Response:
    try:
        await engine.archive(project_id)
    except (EngineError, ProjectStoreError, OSError) as exc:
        raise _translate(exc) from exc
    return Response(status_code=204)


def open_folder(folder: Path) -> None:
    """Show a folder in the system file manager (Explorer on Windows). Tests replace this."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])  # noqa: S603 - fixed program, our own path
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
