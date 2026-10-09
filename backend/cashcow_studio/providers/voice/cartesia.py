"""Cartesia (Sonic) adapter: ``POST /tts/sse`` with ``add_timestamps`` on, because Cartesia
returns word timestamps only on its streaming endpoints.

Written against the public REST reference (https://docs.cartesia.ai/api-reference/tts/sse,
checked 2026-10-09):

* headers ``X-API-Key: $CARTESIA_API_KEY`` and ``Cartesia-Version``;
* body ``{model_id, transcript, voice: {mode: "id", id}, language, output_format:
  {container: "raw", encoding: "pcm_s16le", sample_rate: 48000}, add_timestamps: true}``;
* server-sent events ``data: {type: "chunk", data: <base64 pcm>}``,
  ``{type: "timestamps", word_timestamps: {words, start, end}}``, ``{type: "done"}`` and
  ``{type: "error", error}``.

The raw 48 kHz PCM is written straight into the WAV, so no FFmpeg pass is needed. Imports
and health checks work without a key. Not exercised by the test suite beyond the pure
parsers (``@pytest.mark.live`` for the real call).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...audio.aligner import language_code
from ..base import CostEstimate, ProviderError, ProviderHealth, key_is_set
from ._http import (
    decode_audio_field,
    deliver_audio,
    explain_http_error,
    http_client,
    normalise_words,
    require_key,
)
from .base import (
    CloneConsent,
    ProviderCapabilities,
    SynthRequest,
    SynthResult,
    VoiceInfo,
    WordTiming,
)

log = logging.getLogger(__name__)

CARTESIA_ID = "cartesia"
CARTESIA_KEY_ENV = "CARTESIA_API_KEY"
DEFAULT_BASE_URL = "https://api.cartesia.ai"
DEFAULT_MODEL = "sonic-3"
API_VERSION = "2025-04-16"
TOOL_NAME = "Cartesia"


def default_capabilities() -> ProviderCapabilities:
    """Used when ``config/providers.yaml`` has no ``voice.cartesia`` entry."""
    return ProviderCapabilities(
        id=CARTESIA_ID,
        name="Cartesia Sonic",
        adapter="ready",
        clone=True,
        languages=[],
        timestamp_granularity="word",
        billing_unit="character",
        price_per_unit_usd=0.0,
        max_chars=None,
        key_env=CARTESIA_KEY_ENV,
        models=[DEFAULT_MODEL, "sonic-2", "sonic-turbo"],
        default_model=DEFAULT_MODEL,
        docs_url="https://docs.cartesia.ai/api-reference/tts/sse",
        notes="Word timestamps from the streaming endpoint; credit-based plans.",
    )


def build_body(request: SynthRequest, model: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model_id": model,
        "transcript": request.text,
        "voice": {"mode": "id", "id": request.voice_id},
        "language": language_code(request.language),
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": int(request.sample_rate_hz),
        },
        "add_timestamps": True,
    }
    speed = float(request.speed)
    if abs(speed - 1.0) > 0.01:
        if model.startswith("sonic-3"):
            body["speed"] = round(speed, 2)
        else:
            body["speed"] = "slow" if speed < 1.0 else "fast"
    return body


def parse_sse_lines(lines: Any) -> list[dict[str, Any]]:
    """``data: {...}`` lines (one event per line or per blank-separated block) -> events."""
    events: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text or text == "[DONE]":
            return
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if isinstance(payload, dict):
            events.append(payload)

    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].strip())
    flush()
    return events


def collect_events(events: list[dict[str, Any]]) -> tuple[bytes, list[WordTiming]]:
    """Audio bytes and word rows from the event list; raises on an ``error`` event."""
    chunks: list[bytes] = []
    words: list[WordTiming] = []
    for event in events:
        kind = str(event.get("type") or "")
        if kind == "chunk" and event.get("data"):
            chunks.append(decode_audio_field(event["data"], encoding="base64"))
        elif kind == "timestamps":
            stamps = event.get("word_timestamps") or {}
            words.extend(normalise_words(stamps))
        elif kind == "error":
            raise ProviderError(
                f"{TOOL_NAME} reported an error: {event.get('error') or 'unknown error'}"
            )
    return b"".join(chunks), words


class CartesiaVoiceProvider:
    id = CARTESIA_ID

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    @property
    def key_env(self) -> str:
        return self.capabilities.key_env or CARTESIA_KEY_ENV

    @property
    def base_url(self) -> str:
        return (self.capabilities.base_url or DEFAULT_BASE_URL).rstrip("/")

    def model_for(self, request: SynthRequest | None = None) -> str:
        chosen = (request.model if request else "") or self.capabilities.default_model
        return chosen or DEFAULT_MODEL

    def _headers(self, key: str) -> dict[str, str]:
        return {"X-API-Key": key, "Cartesia-Version": API_VERSION}

    # The protocol ----------------------------------------------------------------------

    def list_voices(self) -> list[VoiceInfo]:
        key = require_key(self.key_env, TOOL_NAME)
        with http_client() as client:
            try:
                response = client.get(f"{self.base_url}/voices/", headers=self._headers(key))
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
        rows = payload.get("data") if isinstance(payload, dict) else payload
        voices: list[VoiceInfo] = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("id"):
                voices.append(
                    VoiceInfo(
                        id=str(row["id"]),
                        name=str(row.get("name") or row["id"]),
                        language=str(row.get("language") or ""),
                        description=str(row.get("description") or ""),
                        is_clone=not bool(row.get("is_public", True)),
                    )
                )
        return voices

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        """``POST /voices/clone`` (multipart: clip, name, description, mode, language).

        ``consent`` is required by the app's policy and stays with the channel.
        """
        key = require_key(self.key_env, TOOL_NAME)
        if not samples:
            raise ProviderError("A voice clone needs at least one sample recording.")
        sample = Path(samples[0])
        if not sample.is_file():
            raise ProviderError(f"The sample recording {sample} does not exist.")
        with http_client(timeout_s=300.0) as client:
            try:
                with sample.open("rb") as handle:
                    response = client.post(
                        f"{self.base_url}/voices/clone",
                        headers=self._headers(key),
                        data={
                            "name": name,
                            "description": f"CashCow Studio clone, consent by "
                            f"{consent.consented_by}",
                            "mode": "similarity",
                            "enhance": "false",
                        },
                        files={"clip": (sample.name, handle)},
                    )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
        voice_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        if not voice_id:
            raise ProviderError(f"{TOOL_NAME} did not return a voice id for the clone.")
        return VoiceInfo(
            id=voice_id, name=name, is_clone=True,
            description=f"Cartesia clone; consent recorded by {consent.consented_by} for "
            f"{consent.owner_name}.",
        )

    def synthesize(self, request: SynthRequest) -> SynthResult:
        key = require_key(self.key_env, TOOL_NAME)
        if not request.voice_id:
            raise ProviderError(
                "The channel has no Cartesia voice id (voice > clone link or voice id)."
            )
        model = self.model_for(request)
        with http_client(timeout_s=300.0) as client:
            try:
                with client.stream(
                    "POST",
                    f"{self.base_url}/tts/sse",
                    headers={**self._headers(key), "Accept": "text/event-stream"},
                    json=build_body(request, model),
                ) as response:
                    response.raise_for_status()
                    events = parse_sse_lines(response.iter_lines())
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
        pcm, words = collect_events(events)
        if not pcm:
            raise ProviderError(f"{TOOL_NAME} answered without audio for this sentence.")
        deliver_audio(
            pcm, request.output_path, audio_format="pcm_s16le",
            sample_rate_hz=request.sample_rate_hz, source_sample_rate_hz=request.sample_rate_hz,
        )
        from ...audio.wav import read_wav_info  # noqa: PLC0415 - light, avoids a cycle

        info = read_wav_info(request.output_path)
        characters = len(request.text)
        return SynthResult(
            path=Path(request.output_path),
            duration_s=info.duration_s,
            sample_rate_hz=info.sample_rate,
            channels=info.channels,
            text=request.text,
            words=[w for w in words if w.end_s <= info.duration_s + 0.05],
            timing_source="provider" if words else "none",
            provider=self.id,
            model=model,
            voice_id=request.voice_id,
            characters=characters,
            cost_usd=round(characters * self.capabilities.price_per_unit_usd, 6),
            sentence_id=request.sentence_id,
        )

    def estimate_cost(self, text: str) -> CostEstimate:
        units = len(text)
        price = self.capabilities.price_per_unit_usd
        return CostEstimate(
            provider=self.id, unit="character", units=units, cost_usd=round(units * price, 6),
            note="Credit-based plan; set price_per_unit_usd in config/providers.yaml."
            if not price else f"{units} characters at ${price:g} each.",
        )

    def health(self) -> ProviderHealth:
        if not key_is_set(self.key_env):
            return ProviderHealth(
                provider=self.id, kind="voice", status="not_configured",
                detail=f"Add {self.key_env} in Settings > API keys.",
                key_env=self.key_env, key_set=False, model=self.model_for(),
            )
        return ProviderHealth(
            provider=self.id, kind="voice", status="ok",
            detail="Key set. Ready to synthesise per sentence with word timestamps.",
            key_env=self.key_env, key_set=True, model=self.model_for(),
        )
