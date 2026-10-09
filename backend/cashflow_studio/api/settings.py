"""``GET`` / ``PUT /api/settings``: folder choices and masked API keys.

Raw key values are accepted on ``PUT`` and written to ``<app_data_dir>/.env``; they are
never returned, not even inside an error message. ``null`` (or an empty string) removes a
key. For the three folders, a missing field means "leave as is" and ``null`` or ``""`` means
"back to the default".
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import PATH_KEYS, Settings
from ..storage import settings_store
from ..storage.settings_store import (
    InvalidKeyName,
    InvalidKeyValue,
    SettingsStore,
    validate_key_name,
)
from .deps import SettingsDep, SettingsStoreDep

router = APIRouter(tags=["settings"])


class KeyStatus(BaseModel):
    set: bool
    masked: str


class SettingsView(BaseModel):
    shared_dir: str
    shared_dir_is_default: bool
    projects_dir: str
    exports_dir: str
    keys: dict[str, KeyStatus]


class SettingsUpdate(BaseModel):
    """The ``PUT`` body.

    Key names are checked in :func:`update_settings`, not in a Pydantic validator: a
    validation error raised here would echo the whole ``keys`` dict, raw values included,
    back to the caller.
    """

    shared_dir: str | None = None
    projects_dir: str | None = None
    exports_dir: str | None = None
    keys: dict[str, str | None] | None = None


def _view(settings: Settings, store: SettingsStore) -> SettingsView:
    return SettingsView(
        shared_dir=str(settings.resolved_shared_dir),
        shared_dir_is_default=settings.shared_dir_is_default,
        projects_dir=str(settings.projects_dir),
        exports_dir=str(settings.exports_dir),
        keys={name: KeyStatus(**status) for name, status in store.key_status().items()},
    )


def _folder_value(key: str, raw: str | None) -> str | None:
    """Validate a folder typed by the user; ``None``/blank means "use the default"."""
    if raw is None or not raw.strip():
        return None
    expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
    if not Path(expanded).is_absolute():
        label = key.replace("_", " ")
        raise HTTPException(
            status_code=422,
            detail=f"The {label} must be a full folder path, for example "
            "D:\\CashflowStudio\\shared.",
        )
    return expanded


@router.get("/settings", response_model=SettingsView)
def read_settings(settings: SettingsDep, store: SettingsStoreDep) -> SettingsView:
    return _view(settings, store)


@router.put("/settings", response_model=SettingsView)
def update_settings(
    body: SettingsUpdate, settings: SettingsDep, store: SettingsStoreDep
) -> SettingsView:
    for name in body.keys or ():
        try:
            validate_key_name(name)
        except InvalidKeyName as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Check every folder before changing anything, so a bad one leaves the rest untouched.
    folders = {
        key: _folder_value(key, getattr(body, key))
        for key in PATH_KEYS
        if key in body.model_fields_set
    }

    # One lock for the files and the in-memory settings: two overlapping saves cannot lose
    # each other's change, and settings.json cannot drift from app.state.settings.
    with settings_store.LOCK:
        if body.keys:
            try:
                store.update_keys(body.keys)
            except (InvalidKeyName, InvalidKeyValue) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"The keys file could not be written: {exc}"
                ) from exc

        if folders:
            for key, value in folders.items():
                setattr(settings, key, value)
            settings.ensure_dirs()
            try:
                store.write_paths(settings.paths_as_dict())
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"The settings file could not be written: {exc}"
                ) from exc
        return _view(settings, store)
