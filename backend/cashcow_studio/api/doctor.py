"""``GET /api/doctor``: health checks for the Settings page."""

from __future__ import annotations

from fastapi import APIRouter

from ..doctor import DoctorReport, run_all
from .deps import SettingsDep

router = APIRouter(tags=["doctor"])


@router.get("/doctor", response_model=DoctorReport)
def doctor(settings: SettingsDep) -> DoctorReport:
    return run_all(settings)
