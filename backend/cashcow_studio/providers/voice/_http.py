"""What the cloud voice adapters share: the HTTP client, turning whatever bytes a tool
returns into the pipeline's WAV, and turning whatever timing shape it returns into
:class:`WordTiming` rows. No key value ever reaches a log or an error message.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ...audio.errors import AudioError
from ...audio.ffmpeg import convert_to_wav
from ...audio.wav import is_standard_wav, write_pcm_wav
from ..base import ProviderError, ProviderNotConfigured, scrub_secret
from .base import WordTiming

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120.0
WAV_MAGIC = b"RIFF"
MAX_REDIRECTS = 5
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
DEFAULT_PORTS = {"http": 80, "https": 443}


def require_key(env_name: str, tool_name: str) -> str:
    """The key value from the environment (returned to the caller only, never logged)."""
    value = os.environ.get(env_name, "").strip() if env_name else ""
    if not value:
        raise ProviderNotConfigured(
            f"{tool_name} needs the {env_name or 'API'} key. Add it in Settings > API keys."
        )
    return value


def http_client(timeout_s: float = DEFAULT_TIMEOUT_S) -> Any:
    """A synchronous ``httpx.Client`` (imported here so the adapters import without it).

    Redirects are never followed automatically: httpx keeps custom key headers such as
    ``X-API-Key`` on a redirect to another host. Downloads that may redirect (a CDN link)
    go through :func:`download`, which follows them by hand and drops every header as soon
    as the host changes.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx ships with the anthropic SDK
        raise ProviderNotConfigured(
            "The httpx package is missing, so cloud voice tools cannot be called. Reinstall "
            "the app or run: pip install httpx"
        ) from exc
    return httpx.Client(timeout=timeout_s, follow_redirects=False)


def same_origin(url_a: str, url_b: str) -> bool:
    """True when both URLs share scheme, host and (effective) port."""
    try:
        a, b = urlsplit(str(url_a)), urlsplit(str(url_b))
    except ValueError:
        return False
    if not a.hostname or not b.hostname:
        return False
    scheme_a, scheme_b = a.scheme.lower(), b.scheme.lower()
    try:
        port_a = a.port or DEFAULT_PORTS.get(scheme_a)
        port_b = b.port or DEFAULT_PORTS.get(scheme_b)
    except ValueError:
        return False
    return (scheme_a, a.hostname.lower(), port_a) == (scheme_b, b.hostname.lower(), port_b)


def download(
    client: Any,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    trusted_url: str = "",
    max_redirects: int = MAX_REDIRECTS,
) -> Any:
    """``GET url`` for a file the tool pointed at (audio, subtitles), following redirects
    by hand. ``headers`` (the key headers of the API) are sent only to the origin of
    ``trusted_url``; any other host, including a redirect target, gets a bare request, so a
    storage or CDN host never sees the key and a presigned link is not refused for
    carrying two credentials."""
    current = str(url)
    for _ in range(max_redirects + 1):
        send = dict(headers or {}) if trusted_url and same_origin(current, trusted_url) else {}
        response = client.get(current, headers=send)
        location = None
        if response.status_code in REDIRECT_CODES:
            location = response.headers.get("location")
        if not location:
            return response
        current = urljoin(current, str(location))
    raise ProviderError(f"The link {url} redirected more than {max_redirects} times.")


ERROR_BODY_SCAN_CHARS = 1_000_000
"""How much of an error body is searched for key values before it is cut for a message."""
ERROR_DETAIL_CHARS = 300


def explain_http_error(exc: Exception, tool_name: str, secrets: list[str]) -> str:
    """A plain-English message for a failed request, with every key value scrubbed.

    The body is scrubbed first and cut afterwards: cutting first could split a key that
    sits across the cut, and the half that stays would no longer match."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = ""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            text = str(response.text or "")[:ERROR_BODY_SCAN_CHARS]
        except Exception:  # noqa: BLE001
            text = ""
    detail = text or str(exc) or type(exc).__name__
    for secret in secrets:
        detail = scrub_secret(detail, secret)
    detail = detail[:ERROR_DETAIL_CHARS]
    if status in (401, 403):
        return f"{tool_name} rejected the API key ({status}). Check it in Settings > API keys."
    if status == 429:
        return f"{tool_name} is rate-limiting this account (429). Wait a minute and try again."
    if status is not None and status >= 500:
        return f"{tool_name} had a server problem ({status}). Try again in a moment."
    if status is not None:
        return f"{tool_name} refused the request ({status}): {detail}"
    return f"{tool_name} could not be reached: {detail}"


def decode_audio_field(value: Any, encoding: str = "base64") -> bytes:
    """Audio carried inside JSON as base64 or hex text."""
    if isinstance(value, bytes):
        return value
    text = str(value or "").strip()
    if not text:
        raise ProviderError("The voice tool answered without any audio.")
    if encoding == "hex":
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise ProviderError("The voice tool's audio was not valid hex text.") from exc
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ProviderError("The voice tool's audio was not valid base64 text.") from exc


def deliver_audio(
    data: bytes,
    output_path: Path,
    *,
    audio_format: str,
    sample_rate_hz: int,
    source_sample_rate_hz: int | None = None,
) -> Path:
    """Write ``data`` as the WAV the pipeline wants (``sample_rate_hz`` mono 16-bit).

    ``audio_format`` is what the bytes are: ``pcm`` / ``pcm_s16le`` (raw 16-bit samples at
    ``source_sample_rate_hz``), ``wav``, or any container FFmpeg can read (``mp3``, ``flac``,
    ``ogg`` ...). Anything that is not already the right WAV goes through FFmpeg.
    """
    if not data:
        raise ProviderError("The voice tool returned an empty audio file.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (audio_format or "").lower().strip().lstrip(".")
    if fmt in ("pcm", "pcm_s16le", "raw", "s16le"):
        rate = int(source_sample_rate_hz or sample_rate_hz)
        if rate == sample_rate_hz:
            write_pcm_wav(output_path, data, sample_rate_hz, channels=1)
            return output_path
        raw = output_path.with_name(output_path.name + ".raw.wav")
        write_pcm_wav(raw, data, rate, channels=1)
        try:
            convert_to_wav(raw, output_path, sample_rate=sample_rate_hz)
        except AudioError as exc:
            raise ProviderError(str(exc)) from exc
        finally:
            _unlink(raw)
        return output_path
    if fmt == "wav" or data[:4] == WAV_MAGIC:
        output_path.write_bytes(data)
        if is_standard_wav(output_path, sample_rate_hz):
            return output_path
        original = output_path.with_name(output_path.name + ".orig.wav")
        output_path.replace(original)
        try:
            convert_to_wav(original, output_path, sample_rate=sample_rate_hz)
        except AudioError as exc:
            raise ProviderError(str(exc)) from exc
        finally:
            _unlink(original)
        return output_path
    temp = output_path.with_name(output_path.name + f".{fmt or 'bin'}")
    temp.write_bytes(data)
    try:
        convert_to_wav(temp, output_path, sample_rate=sample_rate_hz)
    except AudioError as exc:
        raise ProviderError(str(exc)) from exc
    finally:
        _unlink(temp)
    return output_path


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def dig(data: Any, path: str) -> Any:
    """``dig({"a": {"b": [1]}}, "a.b.0")`` -> ``1``; ``None`` when any step is missing."""
    current = data
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def normalise_words(
    rows: Any,
    *,
    text_field: str = "text",
    start_field: str = "start",
    end_field: str = "end",
    confidence_field: str = "confidence",
    time_unit: str = "s",
) -> list[WordTiming]:
    """Any list of ``{text, start, end}`` mappings (or a ``{words, start, end}`` column
    layout) -> :class:`WordTiming` rows in seconds, source ``provider``."""
    if isinstance(rows, dict) and isinstance(rows.get("words"), list):
        texts = rows.get("words") or []
        starts = rows.get("start") or rows.get("starts") or []
        ends = rows.get("end") or rows.get("ends") or []
        rows = [
            {text_field: t, start_field: s, end_field: e}
            for t, s, e in zip(texts, starts, ends, strict=False)
        ]
    if not isinstance(rows, list):
        return []
    divisor = 1000.0 if str(time_unit).lower().startswith("m") else 1.0
    words: list[WordTiming] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get(text_field) or row.get("word") or row.get("text") or "").strip()
        start = row.get(start_field, row.get("start_s", row.get("time_begin")))
        end = row.get(end_field, row.get("end_s", row.get("time_end")))
        if not text or start is None or end is None:
            continue
        try:
            start_s = float(start) / divisor
            end_s = float(end) / divisor
        except (TypeError, ValueError):
            continue
        confidence = row.get(confidence_field, row.get("score", 1.0))
        try:
            conf = min(1.0, max(0.0, float(confidence if confidence is not None else 1.0)))
        except (TypeError, ValueError):
            conf = 1.0
        words.append(
            WordTiming(
                text=text,
                start_s=round(max(0.0, start_s), 6),
                end_s=round(max(end_s, start_s, 0.0), 6),
                confidence=conf,
                source="provider",
            )
        )
    return words


def split_segments_into_words(words: list[WordTiming]) -> list[WordTiming]:
    """A tool that returns phrases ("time_begin"/"time_end" per sentence) still gives usable
    word rows: each phrase is cut into its words by character length."""
    from ...audio.estimate import estimate_words  # noqa: PLC0415 - avoid an import cycle

    out: list[WordTiming] = []
    for row in words:
        tokens = row.text.split()
        if len(tokens) <= 1:
            out.append(row)
            continue
        for est in estimate_words(row.text, row.start_s, row.end_s):
            out.append(
                WordTiming(
                    text=est.text, start_s=est.start_s, end_s=est.end_s,
                    confidence=min(row.confidence, 0.6), source="provider",
                )
            )
    return out
