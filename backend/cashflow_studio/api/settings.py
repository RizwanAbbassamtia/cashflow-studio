"""``GET`` / ``PUT /api/settings``: folder choices, masked API keys and the model settings.

Raw key values are accepted on ``PUT`` and written to ``<app_data_dir>/.env``; they are
never returned, not even inside an error message. ``null`` (or an empty string) removes a
key. For the three folders, a missing field means "leave as is" and ``null`` or ``""`` means
"back to the default".

The nested ``llm``, ``research``, ``pipeline`` and ``voice`` objects (docs/M1-M2-CONTRACT.md
section 11) are saved whole under the same keys in ``settings.json``; a missing object means
"leave as is". After a save the running pipeline picks the new choices up at once: the
writing-model client, the research source and the parallel limit are rebuilt on the engine,
also when only an API key changed (the clients hold the key they were built with).
``providers`` is read-only: what each part of the pipeline will use right now.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from ..config import (
    NESTED_KEYS,
    NESTED_MODELS,
    LlmSettings,
    PipelineSettings,
    ResearchSettings,
    Settings,
)
from ..config import PATH_KEYS as SETTINGS_PATH_KEYS
from ..storage import settings_store
from ..storage.settings_store import (
    InvalidKeyName,
    InvalidKeyValue,
    SettingsStore,
    validate_key_name,
)
from .deps import SettingsDep, SettingsStoreDep

log = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

PATH_KEYS = SETTINGS_PATH_KEYS
LLM_MODEL_CHOICES = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5")


class KeyStatus(BaseModel):
    set: bool
    masked: str


class ProviderRow(BaseModel):
    """Mirror of :class:`cashflow_studio.providers.status.PipelineProviderStatus`."""

    id: str
    kind: str
    name: str
    status: str
    detail: str = ""


class VoiceSettingsView(BaseModel):
    """``voice`` as returned: the effective rate, never ``null``."""

    speaking_rate_wpm: int


class SettingsView(BaseModel):
    shared_dir: str
    shared_dir_is_default: bool
    projects_dir: str
    exports_dir: str
    keys: dict[str, KeyStatus]
    llm: LlmSettings
    research: ResearchSettings
    pipeline: PipelineSettings
    voice: VoiceSettingsView
    providers: list[ProviderRow]


class SettingsUpdate(BaseModel):
    """The ``PUT`` body.

    Key names are checked in :func:`update_settings`, not in a Pydantic validator: a
    validation error raised here would echo the whole ``keys`` dict, raw values included,
    back to the caller. The nested objects are plain dicts here for the same reason and are
    validated against the models in ``config.py`` inside the handler.
    """

    shared_dir: str | None = None
    projects_dir: str | None = None
    exports_dir: str | None = None
    keys: dict[str, str | None] | None = None
    llm: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    pipeline: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None


def _effective_llm(settings: Settings) -> LlmSettings:
    """The per-task models and efforts in use: ``config/llm.yaml`` with the user's overrides."""
    try:
        from ..llm.client import settings_llm_overrides
        from ..llm.config import LLMConfig

        config = LLMConfig.load(settings_llm_overrides(settings.app_data_dir))
    except Exception:  # noqa: BLE001 - a broken llm.yaml must not take the settings page down
        log.exception("config/llm.yaml could not be read for the settings page")
        return settings.llm
    tasks = sorted(set(config.models) | set(config.effort) | set(settings.llm.models))
    tasks = [task for task in tasks if task != "default"]
    models = {task: config.model_for(task) for task in tasks}
    effort: dict[str, Any] = {}
    for task in tasks:
        level = config.effort_for(task)
        effort[task] = level if level in ("low", "medium", "high") else "high"
    return LlmSettings(models=models, effort=effort, max_tokens=dict(settings.llm.max_tokens))


def _effective_voice(settings: Settings) -> VoiceSettingsView:
    from ..llm.config import speaking_rate_wpm

    try:
        rate = speaking_rate_wpm(None, settings.voice.speaking_rate_wpm)
    except Exception:  # noqa: BLE001
        rate = settings.voice.speaking_rate_wpm or 150
    return VoiceSettingsView(speaking_rate_wpm=int(rate))


def _provider_rows(settings: Settings) -> list[ProviderRow]:
    try:
        from ..providers.status import pipeline_provider_status

        return [ProviderRow(**row.model_dump()) for row in pipeline_provider_status(settings)]
    except Exception:  # noqa: BLE001 - the page must still load
        log.exception("Provider status could not be built")
        return []


def _view(settings: Settings, store: SettingsStore) -> SettingsView:
    return SettingsView(
        shared_dir=str(settings.resolved_shared_dir),
        shared_dir_is_default=settings.shared_dir_is_default,
        projects_dir=str(settings.projects_dir),
        exports_dir=str(settings.exports_dir),
        keys={name: KeyStatus(**status) for name, status in store.key_status().items()},
        llm=_effective_llm(settings),
        research=settings.research,
        pipeline=settings.pipeline,
        voice=_effective_voice(settings),
        providers=_provider_rows(settings),
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


_NESTED_LABELS = {
    "llm": "model settings",
    "research": "research settings",
    "pipeline": "pipeline settings",
    "voice": "voice settings",
}


def _nested_value(key: str, raw: dict[str, Any]) -> BaseModel:
    """Validate one nested object; the 422 names the field in plain English."""
    model = NESTED_MODELS[key]
    try:
        value = model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())) or key
        raise HTTPException(
            status_code=422,
            detail=f"The {_NESTED_LABELS[key]} could not be saved: {where}: "
            f"{first.get('msg', 'invalid value')}.",
        ) from exc
    if isinstance(value, LlmSettings):
        for task, model_id in value.models.items():
            if model_id not in LLM_MODEL_CHOICES:
                choices = ", ".join(LLM_MODEL_CHOICES)
                raise HTTPException(
                    status_code=422,
                    detail=f"'{model_id}' is not a model this app knows for the {task} job. "
                    f"Choose one of: {choices}.",
                )
    return value


def _apply_runtime(request: Request, settings: Settings) -> None:
    """Hand the new model and provider choices to the running pipeline engine."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return
    try:
        from ..pipeline.bootstrap import refresh_providers

        refresh_providers(engine, settings)
    except Exception:  # noqa: BLE001 - the save succeeded; the next start picks it up
        log.exception("The pipeline could not pick up the new settings; restart the app")


@router.get("/settings", response_model=SettingsView)
def read_settings(settings: SettingsDep, store: SettingsStoreDep) -> SettingsView:
    return _view(settings, store)


@router.put("/settings", response_model=SettingsView)
def update_settings(
    body: SettingsUpdate, request: Request, settings: SettingsDep, store: SettingsStoreDep
) -> SettingsView:
    for name in body.keys or ():
        try:
            validate_key_name(name)
        except InvalidKeyName as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Check every folder and every nested object before changing anything, so a bad one
    # leaves the rest untouched.
    folders = {
        key: _folder_value(key, getattr(body, key))
        for key in PATH_KEYS
        if key in body.model_fields_set
    }
    nested: dict[str, BaseModel] = {}
    for key in NESTED_KEYS:
        raw = getattr(body, key)
        if raw is not None:
            nested[key] = _nested_value(key, raw)

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

        if nested:
            for key, value in nested.items():
                setattr(settings, key, value)
            try:
                store.write_sections(
                    {key: value.model_dump(mode="json") for key, value in nested.items()}
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"The settings file could not be written: {exc}"
                ) from exc
        if nested or body.keys:
            # A replaced API key must reach the running pipeline too, not only a changed
            # model or provider choice: the clients are rebuilt with the new environment.
            _apply_runtime(request, settings)
        return _view(settings, store)
