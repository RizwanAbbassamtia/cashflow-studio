"""The voice provider contract (docs/M1-M2-CONTRACT.md section 7, docs/PLAN.md section 7).

A voice provider turns one sentence of ``speech_text`` into a WAV file plus word timings.
Synthesis is per sentence so sentence boundaries are exact even when a tool's word
alignment is weak; the voice stage (M3) stitches the files and builds the caption clock.
Timings are normalised to :class:`WordTiming` whatever the tool returns; when a tool returns
none, a forced aligner fills them in later and marks ``source="aligner"``.

Adapters live next to this file (``mock.py``, ``ai33.py`` and, from M3, the cloud tools).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from ..base import AdapterState, BillingUnit, CostEstimate, ProviderHealth, ProviderKind

TimestampGranularity = Literal["word", "character", "sentence", "none", "unknown"]
TimingSource = Literal["provider", "aligner", "estimated"]

DEFAULT_SAMPLE_RATE_HZ = 48000
DEFAULT_SPEAKING_RATE_WPM = 150


class ProviderCapabilities(BaseModel):
    """What a voice tool can do and what it costs; read from ``config/providers.yaml``."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: ProviderKind = "voice"
    adapter: AdapterState = "planned"
    clone: bool = Field(default=False, description="can create a voice clone from samples")
    languages: list[str] = Field(default=[], description="supported languages; empty = any")
    timestamp_granularity: TimestampGranularity = "unknown"
    billing_unit: BillingUnit = "character"
    price_per_unit_usd: float = Field(default=0.0, ge=0, description="per billing unit")
    max_chars: int | None = Field(default=None, description="longest text per request")
    key_env: str = Field(default="", pattern=r"^[A-Z0-9_]*$", description="name of the key")
    extra_key_envs: list[str] = Field(
        default=[], description="other environment variables the tool needs (never values)"
    )
    docs_url: str = ""
    notes: str = ""
    verified_on: str | None = Field(
        default=None, description="date (YYYY-MM-DD) the prices and limits were last checked"
    )
    models: list[str] = Field(default=[], description="model ids the tool offers")
    default_model: str = Field(default="", description="model used when the channel names none")
    base_url: str = Field(default="", description="API root; empty = the adapter's default")
    http: dict[str, Any] = Field(
        default={},
        description="request recipe for the generic HTTP adapter (url, method, headers, "
        "body_template, response); see config/providers.yaml",
    )
    options: dict[str, Any] = Field(
        default={},
        description="adapter-specific settings from config/providers.yaml (ai33: poll_paths, "
        "poll_interval_s, poll_timeout_s, voice_providers, default_voice_prefix); never keys",
    )


class VoiceInfo(BaseModel):
    id: str
    name: str
    language: str = ""
    is_clone: bool = False
    preview_url: str = ""
    description: str = ""
    gender: str = ""
    tags: list[str] = []


class CloneConsent(BaseModel):
    """Stored with every clone: only the team's own enrolled voices may be cloned."""

    owner_name: str = Field(min_length=1, description="whose voice this is")
    consented_by: str = Field(min_length=1, description="who recorded the consent")
    consented_at: datetime
    statement: str = Field(default="", description="the text the owner agreed to")


class WordTiming(BaseModel):
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: TimingSource = "provider"

    @model_validator(mode="after")
    def _end_after_start(self) -> WordTiming:
        if self.end_s < self.start_s:
            raise ValueError(
                f"the word {self.text!r} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self


class SynthRequest(BaseModel):
    """One sentence to synthesise. ``text`` is the speech-normalised form from speech.json."""

    text: str = Field(min_length=1)
    output_path: Path = Field(description="where the WAV is written")
    voice_id: str = Field(default="", description="the tool's voice or clone id")
    language: str = "English"
    model: str = ""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    style: str = ""
    speaking_rate_wpm: int = Field(
        default=DEFAULT_SPEAKING_RATE_WPM,
        ge=60,
        le=400,
        description="used for duration and cost estimates",
    )
    sample_rate_hz: int = Field(default=DEFAULT_SAMPLE_RATE_HZ, ge=8000, le=192000)
    sentence_id: str | None = Field(default=None, description="id from speech.json")

    def cache_key(self, provider: str) -> str:
        """Content hash of everything that changes the audio, so edits never re-bill."""
        payload = {
            "provider": provider,
            "text": self.text,
            "voice_id": self.voice_id,
            "language": self.language,
            "model": self.model,
            "speed": self.speed,
            "style": self.style,
            "sample_rate_hz": self.sample_rate_hz,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class SynthResult(BaseModel):
    path: Path
    duration_s: float = Field(ge=0)
    sample_rate_hz: int
    channels: int = 1
    text: str
    words: list[WordTiming] = []
    timing_source: TimingSource | Literal["none"] = "none"
    provider: str
    model: str = ""
    voice_id: str = ""
    characters: int = Field(default=0, ge=0, description="characters billed")
    cost_usd: float = Field(default=0.0, ge=0)
    cached: bool = False
    sentence_id: str | None = None


@runtime_checkable
class VoiceProvider(Protocol):
    """Every voice adapter implements exactly this; the voice stage talks to nothing else."""

    id: str
    capabilities: ProviderCapabilities

    def list_voices(self) -> list[VoiceInfo]: ...

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo: ...

    def synthesize(self, request: SynthRequest) -> SynthResult: ...

    def estimate_cost(self, text: str) -> CostEstimate: ...

    def health(self) -> ProviderHealth: ...


def count_words(text: str) -> int:
    return len(text.split())


def estimate_speech_seconds(
    text: str, speaking_rate_wpm: int = DEFAULT_SPEAKING_RATE_WPM, speed: float = 1.0
) -> float:
    """How long ``text`` takes to say at ``speaking_rate_wpm`` words per minute times ``speed``."""
    words = count_words(text)
    if words == 0:
        return 0.0
    rate = max(speaking_rate_wpm * speed, 1.0)
    return round(words * 60.0 / rate, 3)
