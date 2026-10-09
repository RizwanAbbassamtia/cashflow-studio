"""ai33: the team's voice-clone tool. A stub until its API documentation arrives.

Every call raises :class:`ProviderNotConfigured` with the same plain message. ``health()``
and ``capabilities`` work, so the Settings page can list the tool and its state. The
capabilities come from ``config/providers.yaml`` (entry ``voice.ai33``); fill in languages,
prices and limits there as they become known, without touching code.
"""

from __future__ import annotations

from pathlib import Path

from ..base import CostEstimate, ProviderHealth, ProviderNotConfigured, key_is_set
from .base import CloneConsent, ProviderCapabilities, SynthRequest, SynthResult, VoiceInfo

AI33_ID = "ai33"
AI33_KEY_ENV = "AI33_API_KEY"
AI33_PENDING = (
    "ai33: API details pending. The ai33 voice tool has no API documentation yet, so "
    "Cashflow Studio cannot send text to it. Until the adapter arrives, set the voice stage "
    "to 'manual' and add your own recording, or use the mock voice (CFS_VOICE_PROVIDER=mock) "
    "for a silent test run."
)


def default_capabilities() -> ProviderCapabilities:
    """Used when ``config/providers.yaml`` has no ``voice.ai33`` entry."""
    return ProviderCapabilities(
        id=AI33_ID,
        name="ai33 voice clone",
        adapter="stub",
        clone=True,
        languages=[],
        timestamp_granularity="unknown",
        billing_unit="character",
        price_per_unit_usd=0.0,
        max_chars=None,
        key_env=AI33_KEY_ENV,
        notes="Named on 2026-10-09; website and API documentation still needed.",
    )


class Ai33VoiceProvider:
    id = AI33_ID

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    @property
    def key_env(self) -> str:
        return self.capabilities.key_env or AI33_KEY_ENV

    def list_voices(self) -> list[VoiceInfo]:
        raise ProviderNotConfigured(AI33_PENDING)

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        raise ProviderNotConfigured(AI33_PENDING)

    def synthesize(self, request: SynthRequest) -> SynthResult:
        raise ProviderNotConfigured(AI33_PENDING)

    def estimate_cost(self, text: str) -> CostEstimate:
        raise ProviderNotConfigured(AI33_PENDING)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.id,
            kind="voice",
            status="not_configured",
            detail=AI33_PENDING,
            key_env=self.key_env,
            key_set=key_is_set(self.key_env),
        )
