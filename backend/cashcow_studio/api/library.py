"""library endpoints (filled in during the M5A wave; see docs/M5A-CONTRACT.md)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["library"])
