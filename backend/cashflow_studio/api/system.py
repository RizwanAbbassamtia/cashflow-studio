"""``GET /api/system/info``: versions and the folders in use."""

from __future__ import annotations

import platform

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from .deps import SettingsDep

router = APIRouter(prefix="/system", tags=["system"])


class SystemInfo(BaseModel):
    version: str
    platform: str
    python: str
    app_data_dir: str
    shared_dir: str
    shared_dir_is_default: bool
    projects_dir: str
    exports_dir: str


@router.get("/info", response_model=SystemInfo)
def system_info(settings: SettingsDep) -> SystemInfo:
    return SystemInfo(
        version=__version__,
        platform=f"{platform.system()} {platform.release()}".strip(),
        python=platform.python_version(),
        app_data_dir=str(settings.app_data_dir),
        shared_dir=str(settings.resolved_shared_dir),
        shared_dir_is_default=settings.shared_dir_is_default,
        projects_dir=str(settings.projects_dir),
        exports_dir=str(settings.exports_dir),
    )
