"""Builds the voice and image providers a pipeline run uses, and reports their state.

Selection: ``CCS_VOICE_PROVIDER`` and ``CCS_IMAGE_PROVIDER`` (environment), else a
``voice_provider`` / ``image_provider`` attribute on the settings object if one exists, else
``mock``. Tests set nothing and get the mocks, so they never touch the network. For the
voice stage, :func:`voice_provider_for_channel` adds one more step (docs/M3-M4-CONTRACT.md
section 1): when nothing names a provider, a channel whose Voice tab picks ai33 gets the
ai33 adapter, so choosing it in Channel Setup is enough. Other tools with an adapter
(``minimax``, ``cartesia``) run only when ``CCS_VOICE_PROVIDER`` names them.

``build_providers`` never raises: an unknown or not-yet-built provider becomes an
"unavailable" stand-in whose calls raise :class:`ProviderNotConfigured` with a plain
message, and whose ``health()`` explains the problem on the Doctor and Settings pages.
Nothing here imports an SDK at import time.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .base import (
    AdapterState,
    CostEstimate,
    HealthStatus,
    ProviderHealth,
    ProviderKind,
    ProviderNotConfigured,
    key_is_set,
    scrub_secret,
)
from .catalog import ProviderCatalog, load_catalog
from .image.base import ImageCapabilities, ImageProvider, ImageRequest, ImageResult
from .image.gemini import GeminiImageProvider
from .image.mock import MockImageProvider
from .voice.ai33 import Ai33VoiceProvider
from .voice.base import (
    CloneConsent,
    ProviderCapabilities,
    SynthRequest,
    SynthResult,
    VoiceInfo,
    VoiceProvider,
)
from .voice.cartesia import CartesiaVoiceProvider
from .voice.generic_http import GENERIC_HTTP_ID, GenericHttpVoiceProvider
from .voice.minimax import MinimaxVoiceProvider
from .voice.mock import MockVoiceProvider

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "mock"
ENV_VARS: dict[str, str] = {
    "voice": "CCS_VOICE_PROVIDER",
    "image": "CCS_IMAGE_PROVIDER",
}
VOICE_ADAPTERS: dict[str, Callable[..., VoiceProvider]] = {
    MockVoiceProvider.id: MockVoiceProvider,
    Ai33VoiceProvider.id: Ai33VoiceProvider,
    MinimaxVoiceProvider.id: MinimaxVoiceProvider,
    CartesiaVoiceProvider.id: CartesiaVoiceProvider,
    GENERIC_HTTP_ID: GenericHttpVoiceProvider,
}


def wired_by_config(capabilities: ProviderCapabilities | None) -> bool:
    """True when the catalogue entry carries an ``http`` recipe with a URL: such a tool runs
    through the generic HTTP adapter without code (an override even for ai33, which has
    its own adapter)."""
    return bool(capabilities and str((capabilities.http or {}).get("url") or "").strip())


IMAGE_ADAPTERS: dict[str, Callable[..., ImageProvider]] = {
    MockImageProvider.id: MockImageProvider,
    GeminiImageProvider.id: GeminiImageProvider,
}


def provider_choice(kind: str, settings: Any = None, override: str | None = None) -> str:
    """Which provider id to use for ``kind``: explicit override, env var, settings, ``mock``."""
    for candidate in (
        override,
        os.environ.get(ENV_VARS[kind]),
        getattr(settings, f"{kind}_provider", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return DEFAULT_PROVIDER


def known_providers(kind: str, catalog: ProviderCatalog | None = None) -> list[str]:
    """Ids a user may choose for ``kind``: everything with an adapter or a catalogue entry."""
    catalog = catalog or load_catalog()
    adapters = VOICE_ADAPTERS if kind == "voice" else IMAGE_ADAPTERS
    entries = catalog.voice if kind == "voice" else catalog.image
    return sorted(set(adapters) | set(entries))


# Stand-ins for providers that cannot run -----------------------------------------------


class _Unavailable:
    """Shared behaviour: every call raises the reason; ``health`` reports it."""

    kind: ProviderKind

    def __init__(
        self, provider_id: str, reason: str, status: HealthStatus, key_env: str = ""
    ) -> None:
        self.id = provider_id
        self.reason = reason
        self.status: HealthStatus = status
        self.key_env = key_env

    def _refuse(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(self.reason)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.id,
            kind=self.kind,
            status=self.status,
            detail=self.reason,
            key_env=self.key_env,
            key_set=key_is_set(self.key_env),
        )


class UnavailableVoiceProvider(_Unavailable):
    kind: ProviderKind = "voice"

    def __init__(
        self,
        provider_id: str,
        reason: str,
        status: HealthStatus = "not_configured",
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        super().__init__(provider_id, reason, status, capabilities.key_env if capabilities else "")
        self.capabilities = capabilities or ProviderCapabilities(
            id=provider_id, name=provider_id, adapter="planned"
        )

    def list_voices(self) -> list[VoiceInfo]:
        raise self._refuse()

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        raise self._refuse()

    def synthesize(self, request: SynthRequest) -> SynthResult:
        raise self._refuse()

    def estimate_cost(self, text: str) -> CostEstimate:
        raise self._refuse()


class UnavailableImageProvider(_Unavailable):
    kind: ProviderKind = "image"

    def __init__(
        self,
        provider_id: str,
        reason: str,
        status: HealthStatus = "not_configured",
        capabilities: ImageCapabilities | None = None,
    ) -> None:
        super().__init__(provider_id, reason, status, capabilities.key_env if capabilities else "")
        self.capabilities = capabilities or ImageCapabilities(
            id=provider_id, name=provider_id, adapter="planned"
        )

    def generate(self, request: ImageRequest) -> ImageResult:
        raise self._refuse()

    def estimate_cost(self, count: int = 1, size: str = "2K") -> CostEstimate:
        raise self._refuse()


# Building ---------------------------------------------------------------------------------


def _unknown_reason(kind: str, name: str, catalog: ProviderCatalog) -> str:
    choices = ", ".join(known_providers(kind, catalog))
    return (
        f"{name!r} is not a known {kind} provider. Choose one of: {choices} "
        f"(set {ENV_VARS[kind]} or pick it in Settings)."
    )


def _planned_reason(kind: str, name: str) -> str:
    return (
        f"The {name} {kind} tool is listed but its adapter arrives in a later version. Use the "
        f"mock provider for now, or set the {kind} stage to 'manual' and add the files yourself."
    )


def build_voice_provider(
    name: str | None = None, settings: Any = None, catalog: ProviderCatalog | None = None
) -> VoiceProvider:
    """The voice provider for ``name`` (or the configured one). Never raises."""
    catalog = catalog or load_catalog()
    chosen = provider_choice("voice", settings, name)
    capabilities = catalog.voice_capabilities(chosen)
    factory = VOICE_ADAPTERS.get(chosen)
    if wired_by_config(capabilities) and chosen != MockVoiceProvider.id:
        factory = GenericHttpVoiceProvider
    if factory is None:
        if capabilities is None:
            reason = _unknown_reason("voice", chosen, catalog)
            log.warning("Voice provider: %s", reason)
            return UnavailableVoiceProvider(chosen, reason, status="fail")
        return UnavailableVoiceProvider(
            chosen, _planned_reason("voice", capabilities.name), "planned", capabilities
        )
    try:
        return factory(capabilities) if capabilities is not None else factory()
    except Exception as exc:  # a broken adapter must not stop the app from starting
        log.exception("Voice provider %s failed to build", chosen)
        reason = f"The {chosen} voice tool could not be set up: {exc}"
        return UnavailableVoiceProvider(chosen, reason, "fail", capabilities)


_channel_voice_providers: dict[str, VoiceProvider] = {}

CHANNEL_VOICE_TOOLS: tuple[str, ...] = (Ai33VoiceProvider.id,)
"""Voice tools a channel's Voice tab selects by itself when no app-wide voice provider is
named (docs/M3-M4-CONTRACT.md section 1). Only ai33: a channel saved earlier with another
tool that has an adapter (``minimax``, ``cartesia``) keeps the offline mock until
``CCS_VOICE_PROVIDER`` names that tool, so it never starts paid calls without notice."""


def voice_choice_is_explicit(settings: Any = None) -> bool:
    """True when ``CCS_VOICE_PROVIDER`` (or a settings ``voice_provider``) names a provider."""
    env = os.environ.get(ENV_VARS["voice"], "")
    attr = getattr(settings, "voice_provider", None)
    return bool(env.strip()) or (isinstance(attr, str) and bool(attr.strip()))


def voice_provider_for_channel(current: Any, tool: str, settings: Any = None) -> Any:
    """The provider the voice stage uses for a channel whose Voice tab names ``tool``.

    Only a tool in :data:`CHANNEL_VOICE_TOOLS` (``ai33``) is picked up from the channel, and
    only while the stage holds the default mock that nothing chose: then its adapter is
    built once, kept for later runs (it remembers things such as the ai33 task-status path)
    and returned. An app-wide choice always wins (``CCS_VOICE_PROVIDER`` or a settings
    ``voice_provider``, even ``mock``); the stage warns about the mismatch only when that
    choice is a real tool, not the mock. Every other tool (``minimax``, ``cartesia``,
    ``fish_audio``, ``other``) keeps the current provider. Never raises.
    """
    wanted = (tool or "").strip().lower()
    current_id = str(getattr(current, "id", "") or "")
    if wanted not in CHANNEL_VOICE_TOOLS or wanted == current_id:
        return current
    if current is not None and (
        current_id != DEFAULT_PROVIDER or voice_choice_is_explicit(settings)
    ):
        return current
    provider = _channel_voice_providers.get(wanted)
    if provider is None:
        provider = build_voice_provider(wanted, settings)
        _channel_voice_providers[wanted] = provider
    return provider


def reset_channel_voice_providers() -> None:
    """Forget the per-channel adapters (used by tests). The app keeps them until it
    restarts, so a change to ``config/providers.yaml`` needs a restart to reach them."""
    _channel_voice_providers.clear()


def build_image_provider(
    name: str | None = None, settings: Any = None, catalog: ProviderCatalog | None = None
) -> ImageProvider:
    """The image provider for ``name`` (or the configured one). Never raises."""
    catalog = catalog or load_catalog()
    chosen = provider_choice("image", settings, name)
    capabilities = catalog.image_capabilities(chosen)
    factory = IMAGE_ADAPTERS.get(chosen)
    if factory is None:
        if capabilities is None:
            reason = _unknown_reason("image", chosen, catalog)
            log.warning("Image provider: %s", reason)
            return UnavailableImageProvider(chosen, reason, status="fail")
        return UnavailableImageProvider(
            chosen, _planned_reason("image", capabilities.name), "planned", capabilities
        )
    try:
        return factory(capabilities) if capabilities is not None else factory()
    except Exception as exc:  # a broken adapter must not stop the app from starting
        log.exception("Image provider %s failed to build", chosen)
        reason = f"The {chosen} image tool could not be set up: {exc}"
        return UnavailableImageProvider(chosen, reason, "fail", capabilities)


def build_providers(
    settings: Any = None, *, image: str | None = None, voice: str | None = None
) -> dict[str, Any]:
    """``{"image": ImageProvider, "voice": VoiceProvider}`` for ``StageContext.providers``.

    ``settings`` is the app ``Settings`` (or ``None``); ``image`` and ``voice`` override the
    environment for one call. Never raises and never touches the network.
    """
    catalog = load_catalog()
    return {
        "image": build_image_provider(image, settings, catalog),
        "voice": build_voice_provider(voice, settings, catalog),
    }


# Status for the Doctor and Settings pages -------------------------------------------------


class ProviderStatus(BaseModel):
    """One row of the "Models and providers" card. Never carries a key value."""

    kind: ProviderKind
    id: str
    name: str
    selected: bool
    adapter: AdapterState
    status: HealthStatus
    detail: str
    key_env: str = ""
    key_set: bool = False
    model: str = ""
    capabilities: dict[str, Any] = {}


def _safe_health(provider: Any, kind: ProviderKind, provider_id: str) -> ProviderHealth:
    key_env = getattr(getattr(provider, "capabilities", None), "key_env", "") or ""
    try:
        return provider.health()
    except Exception as exc:  # a health check must never take the page down
        secret = os.environ.get(key_env, "") if key_env else None
        detail = scrub_secret(str(exc), secret)[:300]
        return ProviderHealth(
            provider=provider_id,
            kind=kind,
            status="fail",
            detail=f"The health check failed: {detail}",
            key_env=key_env,
            key_set=key_is_set(key_env),
        )


def _key_values(capabilities: Any) -> list[str]:
    """The values of the keys a provider uses (only to scrub them out of a detail)."""
    names = [getattr(capabilities, "key_env", "") or ""]
    names.extend(getattr(capabilities, "extra_key_envs", None) or [])
    return [os.environ.get(name, "") for name in names if name]


def list_provider_status(settings: Any = None) -> list[ProviderStatus]:
    """Every known voice and image provider with its state; the selected ones are flagged.

    Local facts only (keys present, software installed): nothing calls the network. (The
    one opt-in exception is ai33 with ``options.health_probe: true``, which then asks ai33
    at most once a minute per key.) No detail ever carries a key value.
    """
    catalog = load_catalog()
    rows: list[ProviderStatus] = []
    kinds: tuple[ProviderKind, ...] = ("voice", "image")
    for kind in kinds:
        selected = provider_choice(kind, settings)
        entries: dict[str, Any] = catalog.voice if kind == "voice" else catalog.image
        for provider_id, capabilities in entries.items():
            if kind == "voice":
                provider: Any = build_voice_provider(provider_id, catalog=catalog)
            else:
                provider = build_image_provider(provider_id, catalog=catalog)
            health = _safe_health(provider, kind, provider_id)
            detail = health.detail
            for secret in _key_values(capabilities):
                detail = scrub_secret(detail, secret)
            rows.append(
                ProviderStatus(
                    kind=kind,
                    id=provider_id,
                    name=capabilities.name,
                    selected=provider_id == selected,
                    adapter=capabilities.adapter,
                    status=health.status,
                    detail=detail,
                    key_env=health.key_env or capabilities.key_env,
                    key_set=health.key_set or key_is_set(capabilities.key_env),
                    model=health.model,
                    capabilities=capabilities.model_dump(mode="json"),
                )
            )
        if selected not in entries:
            rows.append(
                ProviderStatus(
                    kind=kind,
                    id=selected,
                    name=selected,
                    selected=True,
                    adapter="planned",
                    status="fail",
                    detail=_unknown_reason(kind, selected, catalog),
                )
            )
    return rows
