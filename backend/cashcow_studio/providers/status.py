"""What each part of the pipeline will use right now, for Settings > Models and providers.

One row per part (writing model, research source, images, voice) with a status the UI can
colour. Everything here is a local fact (an environment variable set, a module installed, a
mock chosen); nothing calls the network and no key value ever appears in a row.
The front end mirrors :class:`PipelineProviderStatus` in ``frontend/src/types/settings.ts``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Any, Literal

from pydantic import BaseModel

from .registry import (
    CHANNEL_VOICE_TOOLS,
    DEFAULT_PROVIDER,
    ProviderStatus,
    list_provider_status,
    voice_choice_is_explicit,
)

log = logging.getLogger(__name__)

PipelineProviderKind = Literal["llm", "research", "image", "voice"]
PipelineProviderStatusValue = Literal["ready", "mock", "missing_key", "not_configured", "error"]

LLM_PROVIDER_ENV = "CCS_LLM_PROVIDER"
RESEARCH_PROVIDER_ENV = "CCS_RESEARCH_PROVIDER"
ANTHROPIC_KEY_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class PipelineProviderStatus(BaseModel):
    """One row of the provider table. Never carries a key value."""

    id: str
    kind: PipelineProviderKind
    name: str
    status: PipelineProviderStatusValue
    detail: str = ""


def _llm_row(settings: Any) -> PipelineProviderStatus:
    from ..llm.client import provider_name, settings_llm_overrides
    from ..llm.config import LLMConfig

    try:
        app_data_dir = getattr(settings, "app_data_dir", None)
        config = LLMConfig.load(settings_llm_overrides(app_data_dir))
    except Exception as exc:  # noqa: BLE001 - a broken llm.yaml must not take the page down
        return PipelineProviderStatus(
            id="anthropic", kind="llm", name="Claude", status="error",
            detail=f"The model settings could not be read: {exc}",
        )
    chosen = provider_name(None, config)
    writing = config.model_for("script")
    planning = config.model_for("storyboard")
    models = f"Scripts and titles: {writing}. Storyboards and summaries: {planning}."
    if chosen == "mock":
        return PipelineProviderStatus(
            id="mock", kind="llm", name="Claude (sample answers)", status="mock",
            detail="Offline sample answers for testing; nothing is sent to Anthropic. "
            f"Set by {LLM_PROVIDER_ENV}.",
        )
    key_set = any(os.environ.get(name, "").strip() for name in ANTHROPIC_KEY_ENVS)
    if chosen != "anthropic":
        return PipelineProviderStatus(
            id=chosen, kind="llm", name=chosen, status="error",
            detail=f"'{chosen}' is not a known writing model provider. Use anthropic or mock.",
        )
    if not key_set:
        return PipelineProviderStatus(
            id="anthropic", kind="llm", name="Claude (Anthropic)", status="missing_key",
            detail=f"Add ANTHROPIC_API_KEY under API keys. {models}",
        )
    return PipelineProviderStatus(
        id="anthropic", kind="llm", name="Claude (Anthropic)", status="ready", detail=models
    )


def _research_row(settings: Any) -> PipelineProviderStatus:
    from ..research.provider import provider_name

    chosen = provider_name(settings)
    forced = bool(os.environ.get(RESEARCH_PROVIDER_ENV, "").strip())
    note = f" Set by {RESEARCH_PROVIDER_ENV}." if forced else ""
    if chosen == "mock":
        return PipelineProviderStatus(
            id="mock", kind="research", name="Sample data (offline)", status="mock",
            detail="Research uses built-in sample videos; nothing is fetched from YouTube."
            + note,
        )
    installed = importlib.util.find_spec("yt_dlp") is not None
    if not installed:
        return PipelineProviderStatus(
            id="yt-dlp", kind="research", name="YouTube (yt-dlp)", status="error",
            detail="The yt-dlp module is not installed, so competitor scans cannot run. "
            "Reinstall the app or run pip install yt-dlp." + note,
        )
    return PipelineProviderStatus(
        id="yt-dlp", kind="research", name="YouTube (yt-dlp)", status="ready",
        detail="Reads competitor channels with yt-dlp on this computer; no key needed." + note,
    )


def _map_registry_row(row: ProviderStatus) -> PipelineProviderStatus:
    kind: PipelineProviderKind = "voice" if row.kind == "voice" else "image"
    status: PipelineProviderStatusValue
    if row.id == "mock":
        status = "mock"
    elif row.status == "ok":
        status = "ready"
    elif row.status == "warn":
        status = "ready"
    elif row.status == "fail":
        status = "error"
    elif row.status == "not_configured" and row.key_env and not row.key_set:
        status = "missing_key"
    else:
        status = "not_configured"
    detail = row.detail
    if status == "missing_key" and row.key_env and row.key_env not in detail:
        detail = f"{detail} Add {row.key_env} under API keys.".strip()
    return PipelineProviderStatus(
        id=row.id, kind=kind, name=row.name, status=status, detail=detail
    )


def _channel_voice_note(rows: list[ProviderStatus], settings: Any) -> str:
    """While the voice part is the default mock that nothing chose, a channel whose Voice tab
    picks ai33 still runs on ai33 (registry.voice_provider_for_channel); say so on the row."""
    if voice_choice_is_explicit(settings):
        return ""
    names = [
        row.name for row in rows
        if row.kind == "voice" and row.id in CHANNEL_VOICE_TOOLS and row.adapter == "ready"
    ]
    if not names:
        return ""
    return f" Channels whose Voice tab picks {' or '.join(names)} use that tool instead."


def pipeline_provider_status(settings: Any = None) -> list[PipelineProviderStatus]:
    """The four rows (llm, research, image, voice) the Settings card shows. Never raises."""
    rows: list[PipelineProviderStatus] = []
    for build in (_llm_row, _research_row):
        try:
            rows.append(build(settings))
        except Exception as exc:  # noqa: BLE001 - the page must still load
            log.exception("Provider status row failed")
            kind: PipelineProviderKind = "llm" if build is _llm_row else "research"
            rows.append(
                PipelineProviderStatus(
                    id=kind, kind=kind, name=kind, status="error",
                    detail=f"The status check failed: {exc}",
                )
            )
    try:
        every_row = list_provider_status(settings)
        selected = [row for row in every_row if row.selected]
    except Exception:  # noqa: BLE001
        log.exception("Image/voice provider status failed")
        every_row, selected = [], []
    for kind_name in ("image", "voice"):
        matching = [row for row in selected if row.kind == kind_name]
        if matching:
            mapped = _map_registry_row(matching[0])
            if kind_name == "voice" and matching[0].id == DEFAULT_PROVIDER:
                mapped.detail += _channel_voice_note(every_row, settings)
            rows.append(mapped)
        else:
            rows.append(
                PipelineProviderStatus(
                    id=kind_name, kind=kind_name, name=kind_name.title(),  # type: ignore[arg-type]
                    status="not_configured", detail="No provider is selected.",
                )
            )
    return rows
