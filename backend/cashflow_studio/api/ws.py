"""WebSocket event stream at /api/ws (filled in by the pipeline agent in M1)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["events"])
