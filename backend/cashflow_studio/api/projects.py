"""Project endpoints (filled in by the pipeline agent in M1). See docs/M1-M2-CONTRACT.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["projects"])
