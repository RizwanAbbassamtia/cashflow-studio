"""Background job status endpoints (filled in by the pipeline agent in M1)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["jobs"])
