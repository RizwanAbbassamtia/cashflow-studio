"""MiniMax Speech (T2A v2) adapter: ``POST /v1/t2a_v2`` with ``subtitle_enable`` so the
tool also returns timing, which this adapter turns into word rows.

Written against the public REST reference (https://platform.minimax.io/docs/api-reference/
speech-t2a-http, checked 2026-10-09):

* ``Authorization: Bearer $MINIMAX_API_KEY``; the account's ``MINIMAX_GROUP_ID`` goes on the
  query string when set;
* body ``{model, text, stream: false, voice_setting: {voice_id, speed, vol, pitch},
  audio_setting: {sample_rate, format, channel}, subtitle_enable: true, language_boost}``;
* answer ``{data: {audio: <hex>, subtitle_file: <url>}, extra_info: {usage_characters,
  audio_length}, base_resp: {status_code, status_msg}}``. The subtitle file is a JSON list of
  ``{text, time_begin, time_end}`` in milliseconds; phrases longer than one word are cut
  into words by character length (confidence 0.6).

MiniMax offers 8-44.1 kHz; the adapter asks for 44.1 kHz WAV and FFmpeg resamples to the
pipeline's 48 kHz. Imports and health checks work without a key; every network call needs
one. Not exercised by the test suite (``@pytest.mark.live`` only).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..base import CostEstimate, ProviderError, ProviderHealth, ProviderNotConfigured, key_is_set
from ._http import (
    decode_audio_field,
    deliver_audio,
    dig,
    download,
    explain_http_error,
    http_client,
    normalise_words,
    require_key,
    split_segments_into_words,
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

MINIMAX_ID = "minimax"
MINIMAX_KEY_ENV = "MINIMAX_API_KEY"
MINIMAX_GROUP_ENV = "MINIMAX_GROUP_ID"
DEFAULT_BASE_URL = "https://api.minimax.io"
DEFAULT_MODEL = "speech-2.8-hd"
REQUEST_SAMPLE_RATE = 44100
REQUEST_FORMAT = "wav"
LANGUAGE_BOOSTS = {
    "english", "spanish", "french", "russian", "german", "portuguese", "arabic", "italian",
    "japanese", "korean", "indonesian", "vietnamese", "turkish", "dutch", "ukrainian",
    "chinese", "thai", "polish", "romanian", "greek", "czech", "finnish", "hindi",
}
TOOL_NAME = "MiniMax"


def default_capabilities() -> ProviderCapabilities:
    """Used when ``config/providers.yaml`` has no ``voice.minimax`` entry."""
    return ProviderCapabilities(
        id=MINIMAX_ID,
        name="MiniMax Speech",
        adapter="ready",
        clone=True,
        languages=[],
        timestamp_granularity="word",
        billing_unit="character",
        price_per_unit_usd=0.0001,
        max_chars=10000,
        key_env=MINIMAX_KEY_ENV,
        extra_key_envs=[MINIMAX_GROUP_ENV],
        models=[DEFAULT_MODEL, "speech-2.8-turbo"],
        default_model=DEFAULT_MODEL,
        docs_url="https://platform.minimax.io/docs/api-reference/speech-t2a-http",
        notes="Per-sentence synthesis with subtitle timing; rapid voice cloning.",
    )


def language_boost(language: str) -> str:
    text = (language or "").strip()
    return text.title() if text.lower() in LANGUAGE_BOOSTS else "auto"


def build_body(request: SynthRequest, model: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "text": request.text,
        "stream": False,
        "voice_setting": {
            "voice_id": request.voice_id,
            "speed": round(float(request.speed), 2),
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": REQUEST_SAMPLE_RATE,
            "bitrate": 128000,
            "format": REQUEST_FORMAT,
            "channel": 1,
        },
        "subtitle_enable": True,
        "language_boost": language_boost(request.language),
    }
    if request.style:
        body["voice_setting"]["emotion"] = request.style.split(",")[0].strip().lower()[:32]
    return body


def check_base_resp(payload: Any) -> None:
    base = dig(payload, "base_resp") or {}
    code = base.get("status_code", 0) if isinstance(base, dict) else 0
    if code not in (0, None):
        message = str(base.get("status_msg") or "unknown error")
        if code in (1004, 2049):
            raise ProviderNotConfigured(
                f"{TOOL_NAME} rejected the API key ({message}). Check it in Settings > API keys."
            )
        raise ProviderError(f"{TOOL_NAME} refused the request ({code}): {message}")


def words_from_subtitles(rows: Any) -> list[WordTiming]:
    """The subtitle file's ``[{text, time_begin, time_end}]`` (ms) -> word rows."""
    segments = normalise_words(
        rows, text_field="text", start_field="time_begin", end_field="time_end", time_unit="ms"
    )
    return split_segments_into_words(segments)


class MinimaxVoiceProvider:
    id = MINIMAX_ID

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    @property
    def key_env(self) -> str:
        return self.capabilities.key_env or MINIMAX_KEY_ENV

    @property
    def base_url(self) -> str:
        return (self.capabilities.base_url or DEFAULT_BASE_URL).rstrip("/")

    def model_for(self, request: SynthRequest | None = None) -> str:
        chosen = (request.model if request else "") or self.capabilities.default_model
        return chosen or DEFAULT_MODEL

    def _params(self) -> dict[str, str]:
        import os

        group = os.environ.get(MINIMAX_GROUP_ENV, "").strip()
        return {"GroupId": group} if group else {}

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # The protocol ----------------------------------------------------------------------

    def list_voices(self) -> list[VoiceInfo]:
        key = require_key(self.key_env, TOOL_NAME)
        with http_client() as client:
            try:
                response = client.post(
                    f"{self.base_url}/v1/get_voice",
                    params=self._params(),
                    headers=self._headers(key),
                    json={"voice_type": "all"},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - every failure becomes a plain message
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
        check_base_resp(payload)
        voices: list[VoiceInfo] = []
        for group_name, is_clone in (("system_voice", False), ("voice_cloning", True)):
            for row in payload.get(group_name) or []:
                if not isinstance(row, dict):
                    continue
                voice_id = str(row.get("voice_id") or "")
                if voice_id:
                    voices.append(
                        VoiceInfo(
                            id=voice_id,
                            name=str(row.get("voice_name") or voice_id),
                            description=str(row.get("description") or ""),
                            is_clone=is_clone,
                        )
                    )
        return voices

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        """Rapid cloning: upload the sample (``purpose: voice_clone``), then ``/v1/voice_clone``.

        ``consent`` is required by the app's policy (only the team's own voices) and is
        stored by the channel, not sent to MiniMax.
        """
        key = require_key(self.key_env, TOOL_NAME)
        if not samples:
            raise ProviderError("A voice clone needs at least one sample recording.")
        sample = Path(samples[0])
        if not sample.is_file():
            raise ProviderError(f"The sample recording {sample} does not exist.")
        voice_id = "ccs_" + "".join(c for c in name.lower() if c.isalnum())[:24]
        with http_client(timeout_s=300.0) as client:
            try:
                with sample.open("rb") as handle:
                    upload = client.post(
                        f"{self.base_url}/v1/files/upload",
                        params=self._params(),
                        headers={"Authorization": f"Bearer {key}"},
                        data={"purpose": "voice_clone"},
                        files={"file": (sample.name, handle)},
                    )
                upload.raise_for_status()
                uploaded = upload.json()
                check_base_resp(uploaded)
                file_id = dig(uploaded, "file.file_id")
                if not file_id:
                    raise ProviderError(f"{TOOL_NAME} did not return a file id for the sample.")
                response = client.post(
                    f"{self.base_url}/v1/voice_clone",
                    params=self._params(),
                    headers=self._headers(key),
                    json={"file_id": file_id, "voice_id": voice_id},
                )
                response.raise_for_status()
                payload = response.json()
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
        check_base_resp(payload)
        return VoiceInfo(
            id=voice_id,
            name=name,
            is_clone=True,
            description=f"MiniMax clone; consent recorded by {consent.consented_by} for "
            f"{consent.owner_name}.",
        )

    def synthesize(self, request: SynthRequest) -> SynthResult:
        key = require_key(self.key_env, TOOL_NAME)
        if not request.voice_id:
            raise ProviderError(
                "The channel has no MiniMax voice id (voice > clone link or voice id)."
            )
        model = self.model_for(request)
        with http_client() as client:
            try:
                response = client.post(
                    f"{self.base_url}/v1/t2a_v2",
                    params=self._params(),
                    headers=self._headers(key),
                    json=build_body(request, model),
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(explain_http_error(exc, TOOL_NAME, [key])) from exc
            check_base_resp(payload)
            audio_hex = dig(payload, "data.audio")
            if not audio_hex:
                raise ProviderError(f"{TOOL_NAME} answered without audio for this sentence.")
            data = decode_audio_field(audio_hex, encoding="hex")
            words: list[WordTiming] = []
            subtitle_url = dig(payload, "data.subtitle_file")
            if subtitle_url:
                try:
                    # The subtitle file lives on MiniMax's storage host: no key headers.
                    subtitles = download(client, str(subtitle_url))
                    subtitles.raise_for_status()
                    words = words_from_subtitles(subtitles.json())
                except Exception as exc:  # noqa: BLE001 - timing is optional, audio is not
                    log.warning("MiniMax subtitle file could not be read: %s",
                                explain_http_error(exc, TOOL_NAME, [key]))
        deliver_audio(
            data, request.output_path, audio_format=REQUEST_FORMAT,
            sample_rate_hz=request.sample_rate_hz,
        )
        from ...audio.wav import read_wav_info  # noqa: PLC0415 - light, avoids a cycle

        info = read_wav_info(request.output_path)
        characters = int(dig(payload, "extra_info.usage_characters") or len(request.text))
        duration = info.duration_s
        words = [w for w in words if w.end_s <= duration + 0.05]
        return SynthResult(
            path=Path(request.output_path),
            duration_s=duration,
            sample_rate_hz=info.sample_rate,
            channels=info.channels,
            text=request.text,
            words=words,
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
        return CostEstimate(
            provider=self.id,
            unit="character",
            units=units,
            cost_usd=round(units * self.capabilities.price_per_unit_usd, 6),
            note=f"{units} characters at ${self.capabilities.price_per_unit_usd:g} each.",
        )

    def health(self) -> ProviderHealth:
        key_set = key_is_set(self.key_env)
        group_set = key_is_set(MINIMAX_GROUP_ENV)
        if not key_set:
            return ProviderHealth(
                provider=self.id, kind="voice", status="not_configured",
                detail=f"Add {self.key_env} (and {MINIMAX_GROUP_ENV}) in Settings > API keys.",
                key_env=self.key_env, key_set=False, model=self.model_for(),
            )
        detail = "Key set. Ready to synthesise per sentence with timing."
        status = "ok"
        if not group_set:
            detail = f"Key set; {MINIMAX_GROUP_ENV} is missing, which some accounts need."
            status = "warn"
        return ProviderHealth(
            provider=self.id, kind="voice", status=status, detail=detail,  # type: ignore[arg-type]
            key_env=self.key_env, key_set=True, model=self.model_for(),
        )
