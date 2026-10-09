"""``GET /api/jobs/{job_id}``: progress of a background job (research scans and the like).

Jobs live in the in-process registry :mod:`cashflow_studio.pipeline.jobs`; the WebSocket
broadcasts the same changes as ``job.log`` events, this endpoint is for polling.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..pipeline import jobs

router = APIRouter(prefix="/api", tags=["jobs"])


class JobView(BaseModel):
    id: str
    kind: str
    status: jobs.JobStatus
    progress: float
    message: str
    result: Any = None
    error: str | None = None
    created_at: str
    updated_at: str


def _registry(request: Request) -> jobs.JobRegistry:
    engine = getattr(request.app.state, "engine", None)
    return getattr(engine, "jobs", None) or jobs.registry


@router.get("/jobs/{job_id}", response_model=JobView)
async def get_job(job_id: str, request: Request) -> JobView:
    job = _registry(request).get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="That job is not known. It may belong to an earlier run of the app.",
        )
    return JobView(**job.to_dict())
