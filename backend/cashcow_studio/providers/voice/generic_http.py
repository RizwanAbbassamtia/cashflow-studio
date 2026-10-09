"""A voice provider driven entirely by ``config/providers.yaml``, so a tool whose API is
only known later (the team's ai33) can be connected without touching code.

The catalogue entry carries an ``http`` recipe::

    http:
      url: https://api.example.com/v1/tts        # required; empty = not configured
      method: POST                               # POST (default) or GET
      headers:                                   # ${NAME} is replaced by that env var
        Authorization: "Bearer ${AI33_API_KEY}"
        Content-Type: application/json
      body_template:                             # a mapping (sent as JSON) or a string
        text: "{{text}}"                         # {{text}} {{voice}} {{language}} {{speed}}
        voice: "{{voice}}"                       # {{model}} {{style}} {{sample_rate}} too
        language: "{{language}}"
        speed: "{{speed}}"                       # a value that is only a placeholder keeps
      response:                                  # its type (speed -> number)
        kind: json                               # json (default) or audio (body = the file)
        audio_field: data.audio                  # dotted path to the audio text in the JSON
        audio_encoding: base64                   # base64 (default) or hex
        audio_url_field: ""                      # or: dotted path to a URL to download
        audio_url_send_headers: false            # send the key headers to that URL even
                                                 # when it is on another host (default: only
                                                 # the API's own host ever sees the key)
        audio_format: mp3                        # what the bytes are (mp3, wav, pcm, ...)
        pcm_sample_rate: 24000                   # only for raw pcm
        word_timings_path: words                 # dotted path to [{text,start,end}] (optional)
        word_fields: {text: word, start: start, end: end, confidence: score}
        time_unit: s                             # s or ms

Keys are read from the environment by name and never logged. ``key_env`` names the main
key so the Settings and Doctor pages can show whether it is set.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

GENERIC_HTTP_ID = "generic_http"
ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
PLACEHOLDER_NAMES = ("text", "voice", "language", "speed", "model", "style", "sample_rate")


def default_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        id=GENERIC_HTTP_ID,
        name="Generic HTTP voice (set up in config/providers.yaml)",
        adapter="ready",
        clone=False,
        timestamp_granularity="unknown",
        billing_unit="character",
        price_per_unit_usd=0.0,
        key_env="",
        notes="Fill in the http section of config/providers.yaml to connect a tool.",
    )


def placeholder_values(request: SynthRequest, model: str) -> dict[str, Any]:
    return {
        "text": request.text,
        "voice": request.voice_id,
        "language": request.language,
        "speed": float(request.speed),
        "model": model,
        "style": request.style,
        "sample_rate": int(request.sample_rate_hz),
    }


def fill_placeholders(value: Any, values: dict[str, Any]) -> Any:
    """Replace ``{{name}}`` in strings, recursively; a string that is exactly one placeholder
    takes the typed value (so ``"{{speed}}"`` becomes ``1.0``, not ``"1.0"``)."""
    if isinstance(value, str):
        whole = PLACEHOLDER_RE.fullmatch(value.strip())
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: fill_placeholders(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_placeholders(v, values) for v in value]
    return value


def expand_env(text: str, missing: list[str]) -> str:
    """``${NAME}`` -> the environment value; names without a value are collected."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name, "").strip()
        if not value:
            missing.append(name)
        return value

    return ENV_RE.sub(replace, text)


def env_names_in(recipe: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for value in (recipe.get("headers") or {}).values():
        names.extend(ENV_RE.findall(str(value)))
    names.extend(ENV_RE.findall(str(recipe.get("url") or "")))
    return sorted(set(names))


class GenericHttpVoiceProvider:
    """``id`` is the catalogue entry's id (``generic_http`` or, when wired, ``ai33``)."""

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()
        self.id = self.capabilities.id or GENERIC_HTTP_ID

    @property
    def recipe(self) -> dict[str, Any]:
        return dict(self.capabilities.http or {})

    @property
    def key_env(self) -> str:
        if self.capabilities.key_env:
            return self.capabilities.key_env
        names = env_names_in(self.recipe)
        return names[0] if names else ""

    @property
    def configured(self) -> bool:
        return bool(str(self.recipe.get("url") or "").strip())

    def _not_configured(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"The {self.capabilities.name} voice tool has no API address yet. Fill in "
            f"voice.{self.id}.http (url, headers, body_template, response) in "
            "config/providers.yaml, then restart the app."
        )

    def model_for(self, request: SynthRequest | None = None) -> str:
        return (request.model if request else "") or self.capabilities.default_model

    # Request building ---------------------------------------------------------------------

    def build_request(
        self, request: SynthRequest
    ) -> tuple[str, str, dict[str, str], Any, list[str]]:
        """``(method, url, headers, body, secrets)``; raises when the recipe is incomplete."""
        recipe = self.recipe
        if not self.configured:
            raise self._not_configured()
        missing: list[str] = []
        url = expand_env(str(recipe.get("url")), missing)
        headers = {
            str(k): expand_env(str(v), missing) for k, v in (recipe.get("headers") or {}).items()
        }
        if missing:
            names = ", ".join(sorted(set(missing)))
            raise ProviderNotConfigured(
                f"{self.capabilities.name} needs {names}. Add it in Settings > API keys."
            )
        secrets = [
            os.environ.get(name, "") for name in env_names_in(recipe)
            if len(os.environ.get(name, "")) >= 8
        ]
        values = placeholder_values(request, self.model_for(request))
        template = recipe.get("body_template")
        body: Any
        if isinstance(template, str):
            body = fill_placeholders(template, values)
        elif template is None:
            body = {"text": request.text, "voice": request.voice_id,
                    "language": request.language, "speed": float(request.speed)}
        else:
            body = fill_placeholders(template, values)
        method = str(recipe.get("method") or "POST").upper()
        return method, url, headers, body, secrets

    def parse_response(
        self, response: Any, client: Any, headers: dict[str, str], api_url: str = ""
    ) -> tuple[bytes, list[WordTiming], str, int | None]:
        """``(audio bytes, words, audio_format, pcm_sample_rate)`` from the HTTP answer.

        ``api_url`` is the address the request went to: a download link in the answer gets
        the key headers only when it lives on that same host (or when the recipe sets
        ``response.audio_url_send_headers``), never on a storage or CDN host.
        """
        spec = dict(self.recipe.get("response") or {})
        kind = str(spec.get("kind") or "json").lower()
        audio_format = str(spec.get("audio_format") or "").lower()
        pcm_rate = spec.get("pcm_sample_rate")
        pcm_rate = int(pcm_rate) if pcm_rate else None
        if kind == "audio":
            if not audio_format:
                content_type = str(response.headers.get("content-type", "")).lower()
                audio_format = _format_from_content_type(content_type)
            return bytes(response.content), [], audio_format, pcm_rate
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{self.capabilities.name} did not answer with JSON; set response.kind to "
                "'audio' if the answer is the audio file itself."
            ) from exc
        words = self.words_from(payload, spec)
        url_field = str(spec.get("audio_url_field") or "")
        if url_field:
            audio_url = dig(payload, url_field)
            if not audio_url:
                raise ProviderError(
                    f"{self.capabilities.name} answered without '{url_field}' (the audio URL)."
                )
            send_anywhere = bool(spec.get("audio_url_send_headers", False))
            fetched = download(
                client, str(audio_url), headers=headers,
                trusted_url=str(audio_url) if send_anywhere else api_url,
            )
            fetched.raise_for_status()
            if not audio_format:
                audio_format = _format_from_content_type(
                    str(fetched.headers.get("content-type", "")).lower()
                ) or _format_from_url(str(audio_url))
            return bytes(fetched.content), words, audio_format, pcm_rate
        field = str(spec.get("audio_field") or "audio")
        encoded = dig(payload, field)
        if not encoded:
            raise ProviderError(
                f"{self.capabilities.name} answered without '{field}' (the audio data)."
            )
        data = decode_audio_field(encoded, encoding=str(spec.get("audio_encoding") or "base64"))
        return data, words, audio_format or "mp3", pcm_rate

    @staticmethod
    def words_from(payload: Any, spec: dict[str, Any]) -> list[WordTiming]:
        path = str(spec.get("word_timings_path") or "")
        if not path:
            return []
        rows = dig(payload, path)
        fields = spec.get("word_fields") or {}
        return normalise_words(
            rows,
            text_field=str(fields.get("text") or "text"),
            start_field=str(fields.get("start") or "start"),
            end_field=str(fields.get("end") or "end"),
            confidence_field=str(fields.get("confidence") or "confidence"),
            time_unit=str(spec.get("time_unit") or "s"),
        )

    # The protocol ----------------------------------------------------------------------

    def list_voices(self) -> list[VoiceInfo]:
        return []  # a generic recipe describes synthesis only

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        raise ProviderNotConfigured(
            f"Voice cloning is not part of the generic HTTP recipe for {self.capabilities.name}. "
            "Create the clone in the tool's own website and put its voice id in the channel."
        )

    def synthesize(self, request: SynthRequest) -> SynthResult:
        method, url, headers, body, secrets = self.build_request(request)
        with http_client() as client:
            try:
                if method == "GET":
                    params = body if isinstance(body, dict) else None
                    response = client.get(url, headers=headers, params=params)
                elif isinstance(body, dict | list):
                    response = client.request(method, url, headers=headers, json=body)
                else:
                    response = client.request(method, url, headers=headers, content=str(body))
                response.raise_for_status()
                data, words, audio_format, pcm_rate = self.parse_response(
                    response, client, headers, api_url=url
                )
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(
                    explain_http_error(exc, self.capabilities.name, secrets)
                ) from exc
        deliver_audio(
            data, request.output_path, audio_format=audio_format,
            sample_rate_hz=request.sample_rate_hz, source_sample_rate_hz=pcm_rate,
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
            model=self.model_for(request),
            voice_id=request.voice_id,
            characters=characters,
            cost_usd=round(characters * self.capabilities.price_per_unit_usd, 6),
            sentence_id=request.sentence_id,
        )

    def estimate_cost(self, text: str) -> CostEstimate:
        units = len(text)
        return CostEstimate(
            provider=self.id, unit=self.capabilities.billing_unit, units=units,
            cost_usd=round(units * self.capabilities.price_per_unit_usd, 6),
            note="Price from config/providers.yaml (price_per_unit_usd).",
        )

    def health(self) -> ProviderHealth:
        key_env = self.key_env
        if not self.configured:
            return ProviderHealth(
                provider=self.id, kind="voice", status="not_configured",
                detail=f"No API address yet: fill in voice.{self.id}.http in "
                "config/providers.yaml.",
                key_env=key_env, key_set=key_is_set(key_env), model=self.model_for(),
            )
        missing = [name for name in env_names_in(self.recipe) if not key_is_set(name)]
        if missing:
            return ProviderHealth(
                provider=self.id, kind="voice", status="not_configured",
                detail=f"Add {', '.join(missing)} in Settings > API keys.",
                key_env=key_env, key_set=False, model=self.model_for(),
            )
        return ProviderHealth(
            provider=self.id, kind="voice", status="ok",
            detail=f"Set up from config/providers.yaml ({self.recipe.get('url')}).",
            key_env=key_env, key_set=key_is_set(key_env), model=self.model_for(),
        )


def _format_from_content_type(content_type: str) -> str:
    table = {
        "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/wave": "wav", "audio/flac": "flac", "audio/ogg": "ogg", "audio/opus": "ogg",
        "audio/webm": "webm", "audio/mp4": "m4a", "audio/aac": "aac", "audio/l16": "pcm",
        "audio/pcm": "pcm",
    }
    for key, value in table.items():
        if content_type.startswith(key):
            return value
    return ""


def _format_from_url(url: str) -> str:
    tail = url.split("?", 1)[0].rsplit(".", 1)
    return tail[1].lower() if len(tail) == 2 and len(tail[1]) <= 4 else ""


def describe_recipe(capabilities: ProviderCapabilities) -> str:
    """A one-line, key-free description for logs and the Settings page."""
    recipe = capabilities.http or {}
    return json.dumps(
        {"url": recipe.get("url", ""), "method": recipe.get("method", "POST"),
         "keys": env_names_in(dict(recipe))},
    )
