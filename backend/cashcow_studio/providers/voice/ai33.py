"""ai33 (OpenSpeaker) adapter: the team's voice tool, API v3 at ``https://api.ai33.pro``.

Written against the public part of https://ai33.pro/app/api-document (read without a login
on 2026-10-10; the "Common" tab with the task-status endpoint was not readable, see
``docs/providers/ai33.md`` for what still has to be confirmed):

* every call carries ``xi-api-key: $AI33_API_KEY``;
* ``POST /v3/text-to-speech`` (multipart form: ``text``, ``voice_id``, ``speed`` 0.5-1.5,
  ``with_transcript``, ``file_name``) answers ``{"success": true, "task_id": "<uuid>"}``; the
  result is delivered to a webhook or fetched by polling a task endpoint whose exact path
  is unknown, so this adapter tries a configurable list of candidate paths
  (``options.poll_paths``) and remembers the first one whose answer describes the task (it
  repeats the task id, carries a known status word or links to an audio file);
* voice ids are prefixed by engine (``elevenlabs_``, ``minimax_``, ``clone_``, ``edge_``,
  ``kokoro_``, ``vbee_``, ``fishaudio_``); a bare clone id ``123`` becomes ``clone_123``;
* ``GET /v3/voices?provider=...&page=..&page_size=..`` lists voices with ``pagination``;
* ``POST /v3/text-to-speech/voice-clone`` (multipart ``voice_name`` + ``audio_file`` up to
  10 MB) answers ``{"success": true, "data": {"voice_id": "123"}}``.

A submitted task is already paid for, so polling rides out passing trouble: a 5xx, a 429
(after its Retry-After) or a network error counts as "still running" until
``MAX_POLL_FAILURES`` of them come in a row; only a rejected key (401/403), no credits (402)
or a failed task stop at once. Every text the server sends back (error messages, failed-task
reasons) has the key value removed before it reaches an exception, a log line or a health
report.

The task result is read defensively (status words, any audio link, any transcript shape);
the audio is downloaded without the key unless it sits on the API host, converted to the
pipeline's 48 kHz mono WAV with FFmpeg, and the transcript (SRT or JSON) becomes word rows
(``source="provider"``) when it has per-word times, otherwise sentence rows cut into words
by character length (``source="estimated"``, so ``timing.json`` says ``provider_sentence``).
ai33 bills in credits, so every cost estimate is 0 USD with a note. Imports without a key;
nothing here touches the network at import or construction time, and ``health()`` reports
local facts only (``check_key()`` is the explicit network check).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from ...audio.errors import AudioError
from ...audio.estimate import estimate_words
from ...audio.ffmpeg import convert_to_mp3
from ...audio.wav import read_wav_info
from ..base import (
    CostEstimate,
    ProviderError,
    ProviderHealth,
    ProviderNotConfigured,
    key_is_set,
    scrub_secret,
)
from ._http import (
    decode_audio_field,
    deliver_audio,
    dig,
    download,
    explain_http_error,
    http_client,
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

AI33_ID = "ai33"
AI33_KEY_ENV = "AI33_API_KEY"
TOOL_NAME = "ai33"
DEFAULT_BASE_URL = "https://api.ai33.pro"
DOCS_URL = "https://ai33.pro/app/api-document"
API_KEY_HEADER = "xi-api-key"
TTS_PATH = "/v3/text-to-speech"
VOICES_PATH = "/v3/voices"
CLONE_PATH = "/v3/text-to-speech/voice-clone"

VOICE_PREFIXES = ("elevenlabs_", "minimax_", "clone_", "edge_", "kokoro_", "vbee_", "fishaudio_")
DEFAULT_VOICE_PREFIX = "clone_"
DEFAULT_VOICE_PROVIDERS = ["clone", "elevenlabs", "minimax", "fishaudio", "vbee"]
VOICES_PAGE_SIZE = 100
MAX_VOICE_PAGES = 10
VOICE_CACHE_TTL_S = 600.0
HEALTH_CACHE_TTL_S = 60.0
HEALTH_TIMEOUT_S = 5.0

DEFAULT_POLL_PATHS = [
    "/v3/tasks/{task_id}",
    "/v3/task/{task_id}",
    "/v3/common/task/{task_id}",
    "/v3/common/tasks/{task_id}",
    "/common/task?task_id={task_id}",
]
DEFAULT_POLL_INTERVAL_S = 1.5
DEFAULT_POLL_MAX_INTERVAL_S = 5.0
DEFAULT_POLL_TIMEOUT_S = 180.0
POLL_BACKOFF = 1.5
MAX_POLL_WAIT_S = 60.0
"""Upper bound for ``poll_interval_s``, ``poll_max_interval_s`` and a Retry-After wait."""
MAX_POLL_TIMEOUT_S = 3600.0
MAX_POLL_FAILURES = 5
"""Passing failures in a row (network error, 5xx, 429) before a sentence gives up."""
MAX_UNUSABLE_ANSWERS = 4
"""Answers in a row from the remembered status path that say nothing about the task (a 404,
a message without a status) before that path is forgotten and every candidate tried again."""
PROBE_ROUNDS = 4
"""Passes through ``poll_paths`` in which every path answered, none of them about the task,
before the status endpoint counts as unknown (a new task may not be visible at once)."""
MAX_DOWNLOAD_ATTEMPTS = 3
MESSAGE_LIMIT = 300
NOTE_LIMIT = 120

MIN_SPEED = 0.5
MAX_SPEED = 1.5
MAX_CHARS = 1_000_000
MAX_CLONE_SAMPLE_BYTES = 10 * 1024 * 1024
CLONE_MP3_BITRATE_KBPS = 96
CLONE_READY_SUFFIXES = (".mp3", ".wav")

LANGUAGES = [
    "English", "Spanish", "Hindi", "Arabic", "Portuguese", "Indonesian", "Japanese",
    "German", "French", "Russian", "Vietnamese",
]

PENDING_STATUSES = frozenset({
    "pending", "processing", "queued", "running", "in_progress", "inprogress", "waiting",
    "created", "submitted", "started", "accepted", "in_queue", "generating", "scheduled",
})
DONE_STATUSES = frozenset({"completed", "success", "succeeded", "done", "finished", "complete"})
FAILED_STATUSES = frozenset({
    "failed", "fail", "error", "errored", "failure", "cancelled", "canceled", "timeout",
    "timed_out", "rejected", "expired", "aborted",
})
KNOWN_STATUSES = PENDING_STATUSES | DONE_STATUSES | FAILED_STATUSES
TaskState = Literal["done", "pending", "unknown"]

AUDIO_URL_FIELDS = (
    "audio_url", "url", "file_url", "output_url", "result.audio_url", "data.audio_url",
    "result.url", "data.url", "data.file_url", "result.file_url", "output.audio_url",
    "data.output_url", "result.output_url",
)
AUDIO_URL_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".webm")
TEXT_URL_SUFFIXES = (".srt", ".vtt", ".json", ".txt")
NOT_AUDIO_KEY_PARTS = (
    "transcript", "subtitle", "srt", "vtt", "preview", "sample", "help", "doc", "image",
    "thumb", "avatar", "icon", "cover", "webhook", "receive", "callback",
)
"""Keys whose link is something else than the result (a voice preview, a help page)."""
INLINE_AUDIO_FIELDS = ("audio", "audio_base64", "audio_data", "data.audio", "result.audio")
STATUS_FIELDS = (
    "status", "data.status", "result.status", "task.status", "task_status", "state",
    "data.state", "data.task_status",
)
ERROR_FIELDS = (
    "error.message", "error", "message", "msg", "detail", "reason", "data.error",
    "data.message", "result.error", "error_message",
)
TASK_ID_FIELDS = ("task_id", "data.task_id", "taskId", "data.taskId", "task.id", "id", "data.id")
TASK_ID_KEYS = frozenset({"task_id", "taskid", "id", "uuid", "task_uuid", "job_id", "jobid"})
"""Keys under which a status answer may repeat the task id (any depth)."""
KEY_REFUSAL_WORDS = ("api key", "api-key", "api_key", "apikey", "unauthori", "forbidden")
CREDIT_REFUSAL_WORDS = ("credit", "balance", "quota")
TRANSCRIPT_URL_KEYS = (
    "transcript_url", "srt_url", "subtitle_url", "subtitles_url", "transcript_file",
    "subtitle_file", "vtt_url", "words_url", "timestamps_url",
)
TRANSCRIPT_INLINE_KEYS = (
    "transcript", "srt", "subtitles", "words", "segments", "vtt", "alignment", "timestamps",
)
WORD_TEXT_KEYS = ("text", "word", "content", "token")
START_KEYS = ("start", "start_s", "start_time", "startTime", "begin", "time_begin", "from",
              "offset", "start_ms")
END_KEYS = ("end", "end_s", "end_time", "endTime", "time_end", "to", "stop", "end_ms")
CONFIDENCE_KEYS = ("confidence", "score", "probability")

MISSING_KEY_MESSAGE = (
    "Add your ai33 API key in Settings > API keys ({env}). ai33 API keys need a paid "
    "ai33 plan."
)
UNKNOWN_POLL_PATH_MESSAGE = (
    "ai33 task status endpoint not found: none of the status paths in config/providers.yaml "
    "(voice > ai33 > options > poll_paths) answered. Open the Common tab of the ai33 API "
    "document while logged in, put the real path first in poll_paths and restart the app."
)

TIME_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[,.](\d{1,3}))?$")
SRT_ARROW_RE = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
FILE_EXTENSION_RE = re.compile(r"[a-z0-9]{1,4}")

sleep = time.sleep
"""Patched by tests so a poll loop does not wait."""
monotonic = time.monotonic

_HEALTH_CACHE: dict[str, tuple[float, ProviderHealth]] = {}
_HEALTH_LOCK = threading.Lock()
"""``check_key`` results per key (a hash, never the value), shared by every instance: the
Settings page builds a new adapter on each load."""


def default_capabilities() -> ProviderCapabilities:
    """Used when ``config/providers.yaml`` has no ``voice.ai33`` entry."""
    return ProviderCapabilities(
        id=AI33_ID,
        name="ai33 (OpenSpeaker)",
        adapter="ready",
        clone=True,
        languages=list(LANGUAGES),
        timestamp_granularity="sentence",
        billing_unit="credit",
        price_per_unit_usd=0.0,
        max_chars=MAX_CHARS,
        key_env=AI33_KEY_ENV,
        base_url=DEFAULT_BASE_URL,
        docs_url=DOCS_URL,
        notes="The team's voice tool (API v3). Bills in ai33 credits; the task-status path "
        "is confirmed on first use (options.poll_paths).",
        verified_on="2026-10-10",
        options={
            "default_voice_prefix": DEFAULT_VOICE_PREFIX,
            "voice_providers": list(DEFAULT_VOICE_PROVIDERS),
            "poll_paths": list(DEFAULT_POLL_PATHS),
            "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
            "poll_max_interval_s": DEFAULT_POLL_MAX_INTERVAL_S,
            "poll_timeout_s": DEFAULT_POLL_TIMEOUT_S,
            "health_probe": False,
        },
    )


def reset_health_cache() -> None:
    """Forget every ``check_key`` result (used by tests)."""
    with _HEALTH_LOCK:
        _HEALTH_CACHE.clear()


def _health_cache_key(key_env: str, base_url: str, key: str) -> str:
    return hashlib.sha256(f"{key_env}\0{base_url}\0{key}".encode()).hexdigest()


# Pure helpers (unit-tested on their own) ---------------------------------------------------


def scrub(text: str, secrets: Sequence[str] | None = ()) -> str:
    """``text`` with every copy of every key value replaced by ``***``."""
    for secret in secrets or ():
        text = scrub_secret(text, secret)
    return text


def clip(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """One line of at most ``limit`` characters."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 3)].rstrip() + "..."


def server_text(value: Any, secrets: Sequence[str] = (), limit: int = MESSAGE_LIMIT) -> str:
    """A text the server sent (or a message built from one), safe for an error, a log line
    or a health report: key values removed first, then clipped (in that order, so a key cut
    in half by the clip cannot slip through)."""
    return clip(scrub(str(value), secrets), limit)


def _mentions_secret(exc: BaseException, secrets: Sequence[str]) -> bool:
    current: BaseException | None = exc
    for _ in range(10):
        if current is None:
            return False
        text = str(current)
        if scrub(text, secrets) != text:
            return True
        current = current.__cause__ or current.__context__
    return False


def call_without_secrets[T](secrets: Sequence[str], work: Callable[[], T]) -> T:
    """``work()``, with the last guard of every public call: a :class:`ProviderError` that
    (or whose cause) still carries a key value is raised again as a copy with the value
    replaced. Messages are scrubbed where they are built; this catches anything a later
    change forgets. The copy is raised after the handler has finished, so it is not chained
    to the exception that held the key (not even as ``__context__``)."""
    try:
        return work()
    except ProviderError as exc:
        if not _mentions_secret(exc, secrets):
            raise
        clean = type(exc)(scrub(str(exc), secrets))
    raise clean


def normalise_voice_id(value: str, default_prefix: str = DEFAULT_VOICE_PREFIX) -> str:
    """``"123"`` -> ``"clone_123"``; an id that already carries an engine prefix is kept;
    a pasted link keeps only its last path segment (the clone id in the ai33 web app)."""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" in text:
        parts = [p for p in urlsplit(text).path.split("/") if p]
        text = parts[-1] if parts else ""
        if not text:
            return ""
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in VOICE_PREFIXES):
        return text
    prefix = (default_prefix or "").strip()
    if prefix and not prefix.endswith("_"):
        prefix += "_"
    return f"{prefix}{text}"


def engine_of(voice_id: str) -> str:
    """``"clone_123"`` -> ``"clone"``; empty when the id carries no known prefix."""
    lowered = (voice_id or "").lower()
    for prefix in VOICE_PREFIXES:
        if lowered.startswith(prefix):
            return prefix[:-1]
    return ""


def clamp_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    return round(min(MAX_SPEED, max(MIN_SPEED, value)), 2)


def build_form(request: SynthRequest, voice_id: str, file_name: str) -> dict[str, str]:
    """The multipart fields of ``POST /v3/text-to-speech`` (every value a string)."""
    return {
        "text": request.text,
        "voice_id": voice_id,
        "speed": f"{clamp_speed(request.speed):g}",
        "with_transcript": "true",
        "file_name": file_name,
    }


def multipart_fields(form: dict[str, str]) -> dict[str, tuple[None, str]]:
    """httpx sends ``files`` entries with a ``None`` file name as plain multipart fields."""
    return {name: (None, str(value)) for name, value in form.items()}


def is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def iter_strings(payload: Any, key: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a JSON document with the key it sits under, document order."""
    if isinstance(payload, dict):
        for name, value in payload.items():
            yield from iter_strings(value, str(name))
    elif isinstance(payload, list):
        for value in payload:
            yield from iter_strings(value, key)
    elif isinstance(payload, str):
        yield key, payload


def looks_like_audio_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    if path.endswith(TEXT_URL_SUFFIXES):
        return False
    return path.endswith(AUDIO_URL_SUFFIXES) or "audio" in url.lower()


def _names_something_else(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in NOT_AUDIO_KEY_PARTS)


def _audio_link(payload: Any) -> tuple[str, str]:
    """``(link, where it was found)``: the usual field names first, then any other string
    that is an http(s) link to an audio file, skipping keys that name something else (a
    voice preview, a transcript, a help page)."""
    for name in AUDIO_URL_FIELDS:
        value = dig(payload, name)
        if is_http_url(value):
            return str(value).strip(), name
    for key, value in iter_strings(payload):
        if not is_http_url(value) or _names_something_else(key):
            continue
        link = value.strip()
        if looks_like_audio_url(link):
            return link, key
    return "", ""


def find_audio_url(payload: Any) -> str:
    """The first absolute http(s) link to the audio (see :func:`_audio_link`)."""
    return _audio_link(payload)[0]


def find_inline_audio(payload: Any) -> bytes:
    """Audio carried inside the JSON as base64 text (not documented; accepted if it comes)."""
    for name in INLINE_AUDIO_FIELDS:
        value = dig(payload, name)
        if isinstance(value, str) and len(value) > 256 and not is_http_url(value):
            try:
                return decode_audio_field(value)
            except ProviderError:
                return b""
    return b""


def has_audio(payload: Any) -> bool:
    return bool(find_audio_url(payload)) or bool(find_inline_audio(payload))


def _first_string(payload: Any, fields: tuple[str, ...]) -> str:
    for name in fields:
        value = dig(payload, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int | float) and not isinstance(value, bool) and name in (
            "task_id", "data.task_id", "id", "data.id",
        ):
            return str(value)
    return ""


def normalise_status(value: Any) -> str:
    """``" In Progress "`` -> ``"in_progress"``; empty for anything but text."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def task_statuses(payload: Any) -> list[str]:
    """Every status word the answer carries (the envelope's and the task's), field order."""
    words: list[str] = []
    for name in STATUS_FIELDS:
        word = normalise_status(dig(payload, name))
        if word and word not in words:
            words.append(word)
    return words


def task_status(payload: Any) -> str:
    """The task's status word. An envelope may carry its own beside the task's
    (``{"status": "success", "data": {"status": "processing"}}``), so a failure word wins
    over a running word, which wins over a finished word; an unknown word counts only when
    the answer has no known one."""
    words = task_statuses(payload)
    for group in (FAILED_STATUSES, PENDING_STATUSES, DONE_STATUSES):
        for word in words:
            if word in group:
                return word
    return words[0] if words else ""


def task_error(payload: Any, secrets: Sequence[str] = ()) -> str:
    """The server's reason (``error``, ``message``, ``detail`` ...), key values removed."""
    for name in ERROR_FIELDS:
        value = dig(payload, name)
        if isinstance(value, dict):
            value = value.get("message") or value.get("detail") or value.get("msg")
        if isinstance(value, str) and value.strip():
            return server_text(value, secrets)
    return ""


def extract_task_id(payload: Any) -> str:
    return _first_string(payload, TASK_ID_FIELDS)


def refusal_kind(message: str) -> Literal["key", "credits", ""]:
    """``"key"`` / ``"credits"`` when a refusal names one of those (no retry fixes them)."""
    lowered = (message or "").lower()
    if any(word in lowered for word in KEY_REFUSAL_WORDS):
        return "key"
    if any(word in lowered for word in CREDIT_REFUSAL_WORDS):
        return "credits"
    return ""


def refusal(payload: Any, secrets: Sequence[str] = ()) -> ProviderError | None:
    """The error a ``{"success": false, ...}`` answer stands for (``None`` for any other
    answer), with the server's reason and no key value in it."""
    if not (isinstance(payload, dict) and payload.get("success") is False):
        return None
    message = task_error(payload, secrets) or "no reason given"
    kind = refusal_kind(message)
    if kind == "key":
        return ProviderNotConfigured(
            f"{TOOL_NAME} rejected the key: {message}. Check {AI33_KEY_ENV} in "
            "Settings > API keys."
        )
    if kind == "credits":
        return ProviderError(
            f"{TOOL_NAME} reports a credits problem: {message}. Top up your ai33 account."
        )
    return ProviderError(f"{TOOL_NAME} refused the request: {message}")


def check_success(payload: Any, secrets: Sequence[str] = ()) -> None:
    """``{"success": false, ...}`` is a refusal even with HTTP 200."""
    error = refusal(payload, secrets)
    if error is not None:
        raise error


def classify_task(payload: Any, secrets: Sequence[str] = ()) -> TaskState:
    """What a task answer says: ``"done"`` (the audio is there), ``"pending"`` (a running or
    unknown status word; an audio link sent while the task still runs is where the file will
    be, not the file), ``"unknown"`` (neither a status nor audio). Raises
    :class:`ProviderError` for a failed task (with the server's reason, key removed) and for
    a finished task without audio."""
    status = task_status(payload)
    if status in FAILED_STATUSES:
        reason = task_error(payload, secrets) or status
        raise ProviderError(f"{TOOL_NAME} could not make this sentence: {reason}")
    if status in PENDING_STATUSES:
        return "pending"
    if has_audio(payload):
        return "done"
    if status in DONE_STATUSES:
        raise ProviderError(
            f"{TOOL_NAME} reported the sentence as finished but gave no audio link."
        )
    return "pending" if status else "unknown"


def submit_outcome(payload: Any, secrets: Sequence[str] = ()) -> Literal["done", "poll"]:
    """What the JSON answer to ``POST /v3/text-to-speech`` means: ``"done"`` when it already
    carries the audio (and does not say the task still runs), ``"poll"`` when it names a
    task to follow. ``{"success": true, "status": "success", "task_id": ...}`` is an accepted
    request, not a finished sentence."""
    status = task_status(payload)
    if status in FAILED_STATUSES:
        classify_task(payload, secrets)  # raises with the server's reason
    if status not in PENDING_STATUSES and has_audio(payload):
        return "done"
    if extract_task_id(payload):
        return "poll"
    if status in DONE_STATUSES:
        raise ProviderError(
            f"{TOOL_NAME} reported the sentence as finished but gave no audio link."
        )
    if status in PENDING_STATUSES:
        raise ProviderError(
            f"{TOOL_NAME} is still making the sentence but gave no task id to follow it."
        )
    raise ProviderError(
        f"{TOOL_NAME} accepted the sentence but returned neither a task id nor an audio link."
    )


def echoes_task_id(payload: Any, task_id: str, depth: int = 0) -> bool:
    """True when the answer repeats ``task_id`` under an id-like key (up to four levels)."""
    wanted = str(task_id or "").strip()
    if not wanted or depth > 4:
        return False
    if isinstance(payload, dict):
        for name, value in payload.items():
            if isinstance(value, dict | list):
                if echoes_task_id(value, wanted, depth + 1):
                    return True
            elif isinstance(value, str | int) and not isinstance(value, bool):
                if str(name).lower() in TASK_ID_KEYS and str(value).strip() == wanted:
                    return True
    elif isinstance(payload, list):
        return any(echoes_task_id(item, wanted, depth + 1) for item in payload[:50])
    return False


def describes_task(payload: Any, task_id: str) -> bool:
    """True when a status answer is clearly about this task: it repeats the task id,
    carries a known status word, or links to an audio file (under an ``*audio*`` key or
    with an audio extension). A ``{"success": false}`` answer counts only when it repeats
    the id; otherwise it is a refusal of the request, which is what an unknown route (or a
    task that is not visible yet) answers. ``{"message": ...}``, ``{"data": null}`` and the
    like describe nothing, so a candidate path that sends them is not remembered."""
    if not isinstance(payload, dict):
        return False
    if echoes_task_id(payload, task_id):
        return True
    if payload.get("success") is False:
        return False
    if any(word in KNOWN_STATUSES for word in task_statuses(payload)):
        return True
    link, where = _audio_link(payload)
    if link and (
        "audio" in where.lower() or urlsplit(link).path.lower().endswith(AUDIO_URL_SUFFIXES)
    ):
        return True
    return bool(find_inline_audio(payload))


def task_answer(payload: Any, task_id: str) -> dict[str, Any] | None:
    """The mapping that describes this task: the answer itself, or the item of a list answer
    that repeats the task id; ``None`` when the answer is about something else."""
    if isinstance(payload, list):
        for item in payload[:50]:
            if isinstance(item, dict) and echoes_task_id(item, task_id):
                return item
        return None
    return payload if describes_task(payload, task_id) else None


def is_transient_status(status: int) -> bool:
    """A status worth another try: timeouts, rate limits, server trouble (not 501/505)."""
    return status in (408, 425, 429) or (500 <= status <= 599 and status not in (501, 505))


def is_transient_error(exc: BaseException) -> bool:
    """A network failure worth another try (timeout, reset connection, host unreachable)."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a dependency of the app
        return isinstance(exc, OSError)
    return isinstance(
        exc,
        httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError
        | httpx.ProxyError,
    )


def retry_after_seconds(response: Any) -> float | None:
    """The ``Retry-After`` header in seconds (a number or an HTTP date); ``None`` if absent
    or unreadable."""
    try:
        value = str(response.headers.get("retry-after") or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def find_by_key(payload: Any, keys: tuple[str, ...]) -> Any:
    """Depth-first search for the first non-empty value under one of ``keys``."""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}, False, True):
                return value
        for value in payload.values():
            found = find_by_key(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_by_key(value, keys)
            if found is not None:
                return found
    return None


def find_transcript(payload: Any) -> Any:
    """A transcript link (string URL) or an inline transcript (SRT text, JSON text, list or
    mapping); ``None`` when the task result carries none."""
    value = find_by_key(payload, TRANSCRIPT_URL_KEYS)
    if is_http_url(value):
        return str(value).strip()
    value = find_by_key(payload, TRANSCRIPT_INLINE_KEYS)
    if isinstance(value, str | list | dict):
        return value
    return None


def parse_timecode(value: Any) -> float | None:
    """``"00:01:02,500"`` / ``"1:02.5"`` / ``1.25`` -> seconds; ``None`` when unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    match = TIME_RE.match(text)
    if match:
        hours, minutes, seconds, fraction = match.groups()
        total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
        if fraction:
            total += int(fraction.ljust(3, "0")[:3]) / 1000.0
        return float(total)
    try:
        return float(text)
    except ValueError:
        return None


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """SRT or WebVTT cues -> ``[(start_s, end_s, text)]`` (tags stripped, blank cues dropped)."""
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        arrow_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if arrow_index is None:
            continue
        match = SRT_ARROW_RE.search(lines[arrow_index])
        if not match:
            continue
        start = parse_timecode(match.group(1))
        end = parse_timecode(match.group(2))
        if start is None or end is None:
            continue
        body = " ".join(lines[arrow_index + 1:])
        body = HTML_TAG_RE.sub("", body).strip()
        if body:
            cues.append((max(0.0, start), max(end, start, 0.0), body))
    return cues


def _row_text(row: dict[str, Any]) -> str:
    for key in WORD_TEXT_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _row_time(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in row:
            parsed = parse_timecode(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _row_confidence(row: dict[str, Any]) -> float:
    for key in CONFIDENCE_KEYS:
        value = row.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return min(1.0, max(0.0, float(value)))
    return 1.0


def _timed_rows(rows: Any) -> list[tuple[float, float, str, float]]:
    """``[(start, end, text, confidence)]`` from any list of timed mappings."""
    out: list[tuple[float, float, str, float]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = _row_text(row)
        start = _row_time(row, START_KEYS)
        end = _row_time(row, END_KEYS)
        if not text or start is None:
            continue
        if end is None:
            duration = row.get("duration")
            end = start + float(duration) if isinstance(duration, int | float) else start
        out.append((start, end, text, _row_confidence(row)))
    return out


def _unwrap(data: Any) -> Any:
    """``{"data": {...}}`` / ``{"result": {...}}`` wrappers around a transcript."""
    seen = 0
    while isinstance(data, dict) and seen < 4:
        inner = None
        for key in ("data", "result", "transcript", "transcription", "output"):
            value = data.get(key)
            if isinstance(value, dict | list):
                inner = value
                break
        if inner is None or any(k in data for k in ("words", "segments", "sentences")):
            break
        data = inner
        seen += 1
    return data


def collect_word_rows(data: Any) -> list[tuple[float, float, str, float]]:
    """Per-word rows when the transcript has them: a ``words`` list, the ``words`` of each
    segment, or a flat list whose every item is a single token."""
    data = _unwrap(data)
    if isinstance(data, dict):
        words = data.get("words")
        if isinstance(words, list):
            rows = _timed_rows(words)
            if rows:
                return rows
        for key in ("segments", "sentences", "subtitles", "items", "cues"):
            segments = data.get(key)
            if isinstance(segments, list):
                nested: list[tuple[float, float, str, float]] = []
                for segment in segments:
                    if isinstance(segment, dict) and isinstance(segment.get("words"), list):
                        nested.extend(_timed_rows(segment["words"]))
                if nested:
                    return nested
        return []
    if isinstance(data, list):
        nested = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("words"), list):
                nested.extend(_timed_rows(item["words"]))
        if nested:
            return nested
        rows = _timed_rows(data)
        if rows and all(len(text.split()) == 1 for _, _, text, _ in rows):
            return rows
    return []


def collect_segment_rows(data: Any) -> list[tuple[float, float, str, float]]:
    """Sentence or phrase rows (``segments``, ``sentences``, ``subtitles`` or a flat list)."""
    data = _unwrap(data)
    if isinstance(data, dict):
        for key in ("segments", "sentences", "subtitles", "items", "cues", "transcript"):
            rows = _timed_rows(data.get(key))
            if rows:
                return rows
        return []
    if isinstance(data, list):
        return _timed_rows(data)
    return []


def _time_divisor(rows: list[tuple[float, float, str, float]], duration_s: float | None) -> float:
    """Milliseconds are detected against the audio length (or, without one, against the
    fact that one sentence never lasts ten minutes)."""
    if not rows:
        return 1.0
    max_end = max(max(start, end) for start, end, _, _ in rows)
    if duration_s and duration_s > 0:
        return 1000.0 if max_end > duration_s * 10 + 1 else 1.0
    return 1000.0 if max_end > 600 else 1.0


def _segment_words(
    rows: list[tuple[float, float, str, float]], divisor: float
) -> list[WordTiming]:
    """Sentence rows cut into words by character length; marked ``estimated`` so the
    timing document says ``provider_sentence``."""
    out: list[WordTiming] = []
    for start, end, text, confidence in rows:
        start_s, end_s = start / divisor, max(end, start) / divisor
        tokens = text.split()
        if len(tokens) <= 1:
            out.append(WordTiming(
                text=text, start_s=round(start_s, 6), end_s=round(end_s, 6),
                confidence=min(confidence, 0.6), source="estimated",
            ))
            continue
        for est in estimate_words(text, start_s, end_s):
            out.append(WordTiming(
                text=est.text, start_s=est.start_s, end_s=est.end_s,
                confidence=min(confidence, 0.6), source="estimated",
            ))
    return out


def words_from_transcript(
    value: Any, duration_s: float | None = None
) -> tuple[list[WordTiming], bool]:
    """``(rows, per_word)`` from whatever transcript ai33 returned: SRT/VTT text, JSON text,
    or decoded JSON. ``per_word`` is True when the rows are measured words (``source=
    "provider"``); False when they were cut from sentence cues (``source="estimated"``).
    Anything unreadable gives ``([], False)`` and the stage estimates instead."""
    data: Any = value
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        stripped = data.lstrip("\ufeff").strip()
        if stripped[:1] in "{[":
            try:
                data = json.loads(stripped)
            except ValueError:
                return [], False
        elif "-->" in stripped:
            cues = [(s, e, t, 1.0) for s, e, t in parse_srt(stripped)]
            return _segment_words(cues, 1.0), False
        else:
            return [], False
    try:
        word_rows = collect_word_rows(data)
        if word_rows:
            divisor = _time_divisor(word_rows, duration_s)
            words = [
                WordTiming(
                    text=text, start_s=round(max(0.0, start / divisor), 6),
                    end_s=round(max(end, start, 0.0) / divisor, 6),
                    confidence=confidence, source="provider",
                )
                for start, end, text, confidence in word_rows
            ]
            return words, True
        segment_rows = collect_segment_rows(data)
        if segment_rows:
            return _segment_words(segment_rows, _time_divisor(segment_rows, duration_s)), False
    except (TypeError, ValueError) as exc:  # a strange row must not lose the audio
        log.warning("ai33 transcript could not be read: %s", exc)
    return [], False


def fit_words(words: list[WordTiming], duration_s: float) -> list[WordTiming]:
    """Clamp rows to the audio length; drop rows that start after it ends."""
    out: list[WordTiming] = []
    limit = max(0.0, float(duration_s))
    for word in words:
        if word.start_s > limit + 0.05:
            continue
        start = min(word.start_s, limit)
        end = min(max(word.end_s, start), limit)
        out.append(word.model_copy(update={"start_s": round(start, 6), "end_s": round(end, 6)}))
    return out


def format_from_content_type(content_type: str) -> str:
    table = {
        "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/wave": "wav", "audio/vnd.wave": "wav", "audio/flac": "flac", "audio/ogg": "ogg",
        "audio/opus": "ogg", "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
        "audio/aac": "aac",
    }
    lowered = (content_type or "").lower()
    for key, value in table.items():
        if lowered.startswith(key):
            return value
    return ""


def format_from_url(url: str) -> str:
    """The extension of the link's file name (``.../clip.M4A?sig=1`` -> ``m4a``); empty when
    the last path segment has none (``.../v1.2/a``), so it never becomes a bad file name."""
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    stem, dot, extension = name.rpartition(".")
    extension = extension.lower()
    return extension if dot and stem and FILE_EXTENSION_RE.fullmatch(extension) else ""


def explain_ai33_error(exc: Exception, secrets: Sequence[str]) -> ProviderError:
    """The exception to raise for a failed call: plain English, key values scrubbed,
    :class:`ProviderNotConfigured` for a rejected key."""
    if isinstance(exc, ProviderError):
        return exc
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    retry_after = ""
    if response is not None:
        try:
            retry_after = str(response.headers.get("retry-after") or "").strip()
        except Exception:  # noqa: BLE001
            retry_after = ""
    wait = f" Try again in {retry_after} seconds." if retry_after.isdigit() else ""
    if status in (401, 403):
        return ProviderNotConfigured(
            f"{TOOL_NAME} rejected the key ({status}). Check {AI33_KEY_ENV} in Settings > "
            "API keys; ai33 API keys need a paid ai33 plan."
        )
    if status == 402:
        return ProviderError(
            f"{TOOL_NAME} reports that this account has no credits left (402). Top up your "
            f"ai33 account and try again.{wait}"
        )
    if status == 429:
        return ProviderError(
            f"{TOOL_NAME} is rate-limiting this account (429)."
            + (wait or " Wait a minute and try again.")
        )
    return ProviderError(explain_http_error(exc, TOOL_NAME, list(secrets)))


def _status_error(response: Any) -> Exception:
    """The httpx error of a non-2xx answer (it carries the response for the message)."""
    try:
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return exc
    return ProviderError(f"{TOOL_NAME} answered {response.status_code}.")


def voice_info_from_row(row: Any, provider_name: str = "") -> VoiceInfo | None:
    """One ``GET /v3/voices`` row -> :class:`VoiceInfo` (id kept prefixed)."""
    if not isinstance(row, dict):
        return None
    voice_id = str(row.get("voice_id") or row.get("id") or "").strip()
    if not voice_id:
        return None
    if not engine_of(voice_id) and provider_name:
        voice_id = f"{provider_name}_{voice_id}"
    tags_raw = row.get("tags")
    tags = [str(t) for t in tags_raw if str(t).strip()] if isinstance(tags_raw, list) else []
    description = str(row.get("description") or "").strip() or ", ".join(tags)
    return VoiceInfo(
        id=voice_id,
        name=str(row.get("name") or voice_id),
        language=str(row.get("language") or ""),
        is_clone=voice_id.lower().startswith("clone_"),
        preview_url=str(row.get("preview_url") or ""),
        description=description,
        gender=str(row.get("gender") or ""),
        tags=tags,
    )


def prepare_clone_sample(sample: Path) -> tuple[Path, Path | None]:
    """``(path to upload, temp file to delete afterwards)``: an MP3 or WAV under 10 MB goes
    as it is; anything else is converted to a mono MP3 with FFmpeg."""
    sample = Path(sample)
    if not sample.is_file():
        raise ProviderError(f"The sample recording {sample} does not exist.")
    size = sample.stat().st_size
    if sample.suffix.lower() in CLONE_READY_SUFFIXES and size <= MAX_CLONE_SAMPLE_BYTES:
        return sample, None
    temp_dir = Path(tempfile.mkdtemp(prefix="ccs-ai33-"))
    target = temp_dir / "sample.mp3"
    try:
        convert_to_mp3(sample, target, bitrate_kbps=CLONE_MP3_BITRATE_KBPS)
    except AudioError as exc:
        _remove(target)
        raise ProviderError(f"The sample could not be converted for ai33: {exc}") from exc
    if target.stat().st_size > MAX_CLONE_SAMPLE_BYTES:
        _remove(target)
        raise ProviderError(
            "The sample recording is too long for ai33 (over 10 MB even as MP3). Use a "
            "recording of a few minutes at most."
        )
    return target, target


def _remove(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


# Polling --------------------------------------------------------------------------------------


@dataclass
class PollAnswer:
    """One status request, sorted: ``task`` (an answer about this task), ``unusable`` (about
    something else: a 404, a message without a status, not JSON), ``transient`` (network,
    5xx, 429: ask again) or ``refused`` (401/402/403, or a refusal that names the key or the
    credits). ``note`` is a short plain-English summary with no key value."""

    kind: Literal["task", "unusable", "transient", "refused"]
    payload: Any = None
    note: str = ""
    error: ProviderError | None = None
    retry_after_s: float | None = None


@dataclass
class ProbeResult:
    """One pass over ``poll_paths``: the first path whose answer describes the task, or
    what each path answered."""

    path: str | None = None
    answer: PollAnswer | None = None
    tried: list[tuple[str, PollAnswer]] = field(default_factory=list)

    def _only(self, kind: str) -> bool:
        return bool(self.tried) and all(answer.kind == kind for _, answer in self.tried)

    def _last(self, kind: str) -> PollAnswer | None:
        return next((answer for _, answer in reversed(self.tried) if answer.kind == kind), None)

    @property
    def all_transient(self) -> bool:
        """Every path asked failed for a passing reason (host down, server trouble)."""
        return self._only("transient")

    @property
    def all_refused(self) -> bool:
        """Every path refused the key or the credits: that is the problem, not the path."""
        return self._only("refused")

    @property
    def transient(self) -> PollAnswer | None:
        """The last passing failure of the pass (its error and Retry-After)."""
        return self._last("transient")

    @property
    def refused(self) -> PollAnswer | None:
        return self._last("refused")

    def summary(self) -> str:
        notes = [f"{path}: {answer.note}" for path, answer in self.tried]
        return clip("; ".join(notes), 2 * MESSAGE_LIMIT)


# The adapter ------------------------------------------------------------------------------------


class Ai33VoiceProvider:
    id = AI33_ID

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()
        self._lock = threading.Lock()
        self._poll_path: str | None = None
        self._confirmed_path: str | None = None
        self._voices_cache: dict[str, tuple[float, list[VoiceInfo]]] = {}

    # Configuration ---------------------------------------------------------------------

    @property
    def key_env(self) -> str:
        return self.capabilities.key_env or AI33_KEY_ENV

    @property
    def base_url(self) -> str:
        return (self.capabilities.base_url or DEFAULT_BASE_URL).rstrip("/")

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.capabilities.options or {})

    def _option_float(self, name: str, default: float, minimum: float, maximum: float) -> float:
        """A number from ``options``, within ``[minimum, maximum]``; the default when it is
        missing, not a number or infinite (``.inf`` in YAML must not hang a run)."""
        value = self.options.get(name, default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(number):
            return default
        return min(maximum, max(minimum, number))

    def _option_list(self, name: str, default: list[str]) -> list[str]:
        value = self.options.get(name)
        if isinstance(value, str):
            value = [part for part in re.split(r"[,\s]+", value) if part]
        if not isinstance(value, list):
            return list(default)
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned or list(default)

    @property
    def poll_paths(self) -> list[str]:
        return self._option_list("poll_paths", DEFAULT_POLL_PATHS)

    @property
    def poll_interval_s(self) -> float:
        return self._option_float(
            "poll_interval_s", DEFAULT_POLL_INTERVAL_S, 0.1, MAX_POLL_WAIT_S
        )

    @property
    def poll_max_interval_s(self) -> float:
        return max(
            self.poll_interval_s,
            self._option_float(
                "poll_max_interval_s", DEFAULT_POLL_MAX_INTERVAL_S, 0.1, MAX_POLL_WAIT_S
            ),
        )

    @property
    def poll_timeout_s(self) -> float:
        return self._option_float(
            "poll_timeout_s", DEFAULT_POLL_TIMEOUT_S, 1.0, MAX_POLL_TIMEOUT_S
        )

    @property
    def voice_providers(self) -> list[str]:
        return self._option_list("voice_providers", DEFAULT_VOICE_PROVIDERS)

    @property
    def default_voice_prefix(self) -> str:
        value = self.options.get("default_voice_prefix", DEFAULT_VOICE_PREFIX)
        return str(value).strip() if value is not None else ""

    @property
    def voice_cache_ttl_s(self) -> float:
        return self._option_float("voice_cache_ttl_s", VOICE_CACHE_TTL_S, 0.0, 86400.0)

    @property
    def health_probe(self) -> bool:
        """``options.health_probe``: off unless switched on (``health()`` then calls
        :meth:`check_key`, which asks ai33 at most once a minute per key)."""
        value = self.options.get("health_probe", False)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    @property
    def remembered_poll_path(self) -> str | None:
        return self._poll_path

    def _key(self) -> str:
        value = os.environ.get(self.key_env, "").strip()
        if not value:
            raise ProviderNotConfigured(MISSING_KEY_MESSAGE.format(env=self.key_env))
        return value

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {API_KEY_HEADER: key}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _file_name(self, request: SynthRequest) -> str:
        return "ccs-" + request.cache_key(self.id)[:24]

    def model_for(self, request: SynthRequest | None = None, voice_id: str = "") -> str:
        chosen = (request.model if request else "") or self.capabilities.default_model
        return chosen or engine_of(voice_id) or "v3"

    # Polling ---------------------------------------------------------------------------

    def poll_url(self, path: str, task_id: str) -> str:
        text = (path or "").strip()
        if "{task_id}" in text:
            filled = text.replace("{task_id}", quote(task_id, safe=""))
        else:
            filled = text.rstrip("/") + "/" + quote(task_id, safe="")
        if filled.lower().startswith(("http://", "https://")):
            return filled
        return self._url(filled)

    def _remember_poll_path(self, path: str) -> None:
        with self._lock:
            if self._poll_path != path:
                log.info("ai33 task status endpoint: %s", path)
            self._poll_path = path
            self._confirmed_path = path

    def _forget_poll_path(self, path: str) -> None:
        with self._lock:
            if self._poll_path == path:
                self._poll_path = None
                log.warning(
                    "ai33 status path %s gave no usable answer %d times in a row; trying "
                    "every path in poll_paths again.", path, MAX_UNUSABLE_ANSWERS,
                )

    def _ask_status(
        self, client: Any, headers: dict[str, str], path: str, task_id: str,
        secrets: Sequence[str],
    ) -> PollAnswer:
        """One status request, sorted into task / unusable / transient / refused (never
        raises: the caller knows whether the path is confirmed, which decides what a refusal
        means)."""
        try:
            response = client.get(self.poll_url(path, task_id), headers=headers)
        except Exception as exc:  # noqa: BLE001 - sorted below
            error = explain_ai33_error(exc, secrets)
            kind: Literal["unusable", "transient"] = (
                "transient" if is_transient_error(exc) else "unusable"
            )
            return PollAnswer(kind, note=server_text(error, secrets, NOTE_LIMIT), error=error)
        status = int(response.status_code)
        if not 200 <= status < 300:
            error = explain_ai33_error(_status_error(response), secrets)
            note = server_text(error, secrets, NOTE_LIMIT)
            if status in (401, 402, 403):
                return PollAnswer("refused", note=note, error=error)
            if is_transient_status(status):
                return PollAnswer(
                    "transient", note=note, error=error,
                    retry_after_s=retry_after_seconds(response),
                )
            return PollAnswer("unusable", note="not found (404)" if status == 404 else note)
        try:
            payload = response.json()
        except ValueError:
            return PollAnswer("unusable", note=f"an answer that is not JSON ({status})")
        if (
            isinstance(payload, dict)
            and payload.get("success") is False
            and refusal_kind(task_error(payload, secrets))
        ):
            error = refusal(payload, secrets)
            return PollAnswer("refused", note=server_text(error, secrets, NOTE_LIMIT), error=error)
        task = task_answer(payload, task_id)
        if task is None:
            reason = task_error(payload, secrets) if isinstance(payload, dict) else ""
            note = "an answer without a task status" + (
                f": {clip(reason, NOTE_LIMIT)}" if reason else ""
            )
            return PollAnswer("unusable", payload=payload, note=note)
        return PollAnswer("task", payload=task)

    def _probe(
        self, client: Any, headers: dict[str, str], task_id: str, secrets: Sequence[str]
    ) -> ProbeResult:
        """One pass over ``poll_paths``. A 429 ends the pass early: the next one waits."""
        result = ProbeResult()
        for path in self.poll_paths:
            answer = self._ask_status(client, headers, path, task_id, secrets)
            if answer.kind == "task":
                result.path, result.answer = path, answer
                return result
            result.tried.append((path, answer))
            if answer.kind == "transient" and answer.retry_after_s is not None:
                break
        return result

    def _probe_failed(self, task_id: str, probe: ProbeResult) -> ProviderError:
        transient = probe.transient
        if probe.all_transient and transient is not None and transient.error is not None:
            return transient.error
        if self._confirmed_path is not None:  # a path worked earlier in this session
            return ProviderError(
                f"{TOOL_NAME} stopped answering about this sentence (task {task_id}). Last "
                f"answers: {probe.summary()}. Try again in a moment; if it keeps happening, "
                "check poll_paths in config/providers.yaml."
            )
        return ProviderError(f"{UNKNOWN_POLL_PATH_MESSAGE} Last answers: {probe.summary()}.")

    def _timed_out(
        self, task_id: str, seen_pending: bool, last: PollAnswer | None
    ) -> ProviderError:
        within = f"within {self.poll_timeout_s:g} seconds (task {task_id})"
        if seen_pending:
            failed = f" The last check failed: {last.note}." if last and last.note else ""
            return ProviderError(
                f"{TOOL_NAME} did not finish this sentence {within}.{failed} Try again; if "
                "it keeps happening, raise poll_timeout_s in config/providers.yaml."
            )
        if last is not None and last.kind == "transient" and last.error is not None:
            return last.error
        detail = f" Last answers: {last.note}." if last and last.note else ""
        if self._poll_path is None and self._confirmed_path is None:
            return ProviderError(UNKNOWN_POLL_PATH_MESSAGE + detail)
        return ProviderError(
            f"{TOOL_NAME} gave no usable answer about this sentence {within}.{detail} Try "
            "again in a moment."
        )

    def _poll_task(
        self, client: Any, headers: dict[str, str], task_id: str, secrets: Sequence[str]
    ) -> Any:
        """Poll until the task carries its audio; returns that task answer.

        Before a status path is known (or after the remembered one stopped answering about
        tasks), every candidate in ``poll_paths`` is asked in turn and the first answer that
        describes this task (:func:`describes_task`) picks the path; up to
        ``PROBE_ROUNDS`` passes, because a task may not be visible at once. While probing, a
        refusal from one candidate only rules that path out (the POST was just accepted with
        the same key); every candidate refusing, or a refusal from the confirmed path, stops
        at once. Passing trouble (network, 5xx, 429 with its Retry-After) counts as "still
        running" until ``MAX_POLL_FAILURES`` come in a row. Raises on a failed task and at
        ``poll_timeout_s``; only a task that was seen running gets the advice to raise that
        timeout."""
        deadline = monotonic() + self.poll_timeout_s
        interval = self.poll_interval_s
        failures = unusable = clean_rounds = 0
        seen_pending = confirmed_here = False
        last: PollAnswer | None = None
        while True:
            retry_after: float | None = None
            path = self._poll_path
            answer: PollAnswer | None
            if path is None:
                probe = self._probe(client, headers, task_id, secrets)
                answer = probe.answer
                refused = probe.refused
                if probe.path is not None:
                    path = probe.path
                    self._remember_poll_path(path)
                elif probe.all_refused and refused is not None and refused.error is not None:
                    raise refused.error
                else:
                    transient = probe.transient
                    last = PollAnswer(
                        "transient" if probe.all_transient else "unusable",
                        note=probe.summary(),
                        error=transient.error if transient is not None else None,
                    )
                    if transient is not None:
                        failures += 1
                        retry_after = transient.retry_after_s
                        if failures >= MAX_POLL_FAILURES:
                            raise self._probe_failed(task_id, probe)
                    else:
                        clean_rounds += 1
                        if clean_rounds >= PROBE_ROUNDS:
                            raise self._probe_failed(task_id, probe)
            else:
                answer = self._ask_status(client, headers, path, task_id, secrets)
                if answer.kind == "refused" and answer.error is not None:
                    raise answer.error  # the confirmed path: a rejected key or no credits
            if answer is not None and path is not None:
                if answer.kind == "task":
                    state = classify_task(answer.payload, secrets)  # raises on a failed task
                    if state == "done":
                        return answer.payload
                    confirmed_here = True
                    failures = 0
                    if state == "pending":
                        seen_pending, unusable, last = True, 0, None
                    else:
                        unusable += 1
                        reason = task_error(answer.payload, secrets)
                        last = PollAnswer(
                            "unusable", note="an answer without a status"
                            + (f": {clip(reason, NOTE_LIMIT)}" if reason else ""),
                        )
                elif answer.kind == "transient":
                    failures += 1
                    last = answer
                    retry_after = answer.retry_after_s
                    if failures >= MAX_POLL_FAILURES and answer.error is not None:
                        raise answer.error
                else:
                    unusable += 1
                    last = answer
                if unusable >= MAX_UNUSABLE_ANSWERS:
                    if confirmed_here:
                        note = last.note if last is not None else "no status"
                        raise ProviderError(
                            f"{TOOL_NAME} stopped reporting on this sentence (task {task_id}): "
                            f"{note}. Try again in a moment."
                        )
                    self._forget_poll_path(path)
                    unusable = clean_rounds = 0
            if monotonic() >= deadline:
                raise self._timed_out(task_id, seen_pending, last)
            wait = interval
            if retry_after is not None:
                remaining = max(0.0, deadline - monotonic())
                wait = max(interval, min(retry_after, MAX_POLL_WAIT_S, remaining))
            sleep(wait)
            interval = min(interval * POLL_BACKOFF, self.poll_max_interval_s)

    # The protocol ----------------------------------------------------------------------

    def list_voices(self, language: str | None = None) -> list[VoiceInfo]:
        """Every voice of the configured engines (``options.voice_providers``), paged 100 at
        a time, cached in memory for ten minutes. ``language`` is passed to the API as it is
        (ai33 uses tags such as ``vi-VN``)."""
        key = self._key()
        cache_key = (language or "").strip().lower()
        with self._lock:
            entry = self._voices_cache.get(cache_key)
        if entry and monotonic() - entry[0] < self.voice_cache_ttl_s:
            return [voice.model_copy() for voice in entry[1]]
        voices = call_without_secrets([key], lambda: self._all_voices(key, language))
        with self._lock:
            self._voices_cache[cache_key] = (monotonic(), voices)
        return [voice.model_copy() for voice in voices]

    def _all_voices(self, key: str, language: str | None) -> list[VoiceInfo]:
        headers = self._headers(key)
        voices: list[VoiceInfo] = []
        with http_client(timeout_s=60.0) as client:
            for provider_name in self.voice_providers:
                voices.extend(
                    self._voices_for_provider(client, headers, provider_name, language, [key])
                )
        return voices

    def _voices_page(
        self, client: Any, headers: dict[str, str], params: dict[str, Any],
        secrets: Sequence[str],
    ) -> Any:
        try:
            response = client.get(self._url(VOICES_PATH), headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise explain_ai33_error(exc, secrets) from exc
        check_success(payload, secrets)
        return payload

    def _voices_for_provider(
        self,
        client: Any,
        headers: dict[str, str],
        provider_name: str,
        language: str | None,
        secrets: Sequence[str],
    ) -> list[VoiceInfo]:
        voices: list[VoiceInfo] = []
        for page in range(1, MAX_VOICE_PAGES + 1):
            params: dict[str, Any] = {
                "provider": provider_name, "page": page, "page_size": VOICES_PAGE_SIZE,
            }
            if language and language.strip():
                params["language"] = language.strip()
            payload = self._voices_page(client, headers, params, secrets)
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                info = voice_info_from_row(row, provider_name)
                if info is not None:
                    voices.append(info)
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            has_more = bool(pagination.get("has_more")) if isinstance(pagination, dict) else False
            if not has_more:
                break
        return voices

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        """Upload the first sample to ``/v3/text-to-speech/voice-clone``. Refused without a
        consent record (only the team's own enrolled voices may be cloned); the record stays
        with the channel and is never sent to ai33."""
        if (
            not isinstance(consent, CloneConsent)
            or not consent.owner_name.strip()
            or not consent.consented_by.strip()
        ):
            raise ProviderError(
                "A voice may only be cloned with a consent record: fill in the voice owner "
                "and who recorded the consent in the channel's Voice tab first."
            )
        key = self._key()
        if not samples:
            raise ProviderError("A voice clone needs at least one sample recording.")
        clean_name = " ".join((name or "").split()) or "CashCow voice"
        upload_path, temp = prepare_clone_sample(Path(samples[0]))
        try:
            payload = call_without_secrets(
                [key], lambda: self._upload_clone(key, upload_path, clean_name)
            )
        finally:
            _remove(temp)
        voice_id = dig(payload, "data.voice_id") or dig(payload, "voice_id") or dig(
            payload, "data.id"
        )
        if voice_id in (None, ""):
            raise ProviderError(f"{TOOL_NAME} did not return a voice id for the new clone.")
        with self._lock:
            self._voices_cache.clear()
        return VoiceInfo(
            id=normalise_voice_id(str(voice_id), DEFAULT_VOICE_PREFIX),
            name=clean_name,
            is_clone=True,
            description=f"ai33 clone; consent recorded by {consent.consented_by} for "
            f"{consent.owner_name}.",
        )

    def _upload_clone(self, key: str, upload_path: Path, voice_name: str) -> Any:
        secrets = [key]
        mime = "audio/mpeg" if upload_path.suffix.lower() == ".mp3" else "audio/wav"
        with http_client(timeout_s=300.0) as client:
            try:
                with upload_path.open("rb") as handle:
                    response = client.post(
                        self._url(CLONE_PATH),
                        headers=self._headers(key),
                        data={"voice_name": voice_name},
                        files={"audio_file": (upload_path.name, handle, mime)},
                    )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise explain_ai33_error(exc, secrets) from exc
        check_success(payload, secrets)
        return payload

    def synthesize(self, request: SynthRequest) -> SynthResult:
        key = self._key()
        voice_id = normalise_voice_id(request.voice_id, self.default_voice_prefix)
        if not voice_id:
            raise ProviderError(
                "The channel has no ai33 voice id (Voice > Clone link or voice id), for "
                "example clone_123 or minimax_<id>."
            )
        return call_without_secrets([key], lambda: self._synthesize(request, voice_id, key))

    def _synthesize(self, request: SynthRequest, voice_id: str, key: str) -> SynthResult:
        secrets = [key]
        headers = self._headers(key)
        form = build_form(request, voice_id, self._file_name(request))
        words: list[WordTiming] = []
        per_word = False
        with http_client(timeout_s=120.0) as client:
            try:
                response = client.post(
                    self._url(TTS_PATH), headers=headers, files=multipart_fields(form)
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                raise explain_ai33_error(exc, secrets) from exc
            content_type = str(response.headers.get("content-type", "")).lower()
            audio_format = format_from_content_type(content_type)
            transcript: Any = None
            if audio_format or content_type.startswith("audio/"):
                # Direct answer: the body is the audio file (no task, no transcript).
                audio_bytes = bytes(response.content)
                audio_format = audio_format or "mp3"
            else:
                try:
                    payload = response.json()
                except ValueError as exc:
                    shown = server_text(content_type or "no content type", secrets, 80)
                    raise ProviderError(
                        f"{TOOL_NAME} answered with neither audio nor JSON ({shown})."
                    ) from exc
                check_success(payload, secrets)
                if submit_outcome(payload, secrets) == "poll":
                    payload = self._poll_task(client, headers, extract_task_id(payload), secrets)
                audio_bytes, audio_format = self._fetch_audio(client, headers, payload, secrets)
                transcript = find_transcript(payload)
            deliver_audio(
                audio_bytes, request.output_path, audio_format=audio_format,
                sample_rate_hz=request.sample_rate_hz,
            )
            info = read_wav_info(request.output_path)
            if info.frames <= 0:
                _discard(Path(request.output_path))
                raise ProviderError(
                    f"{TOOL_NAME} returned an audio file without any sound for this sentence. "
                    "Try again."
                )
            if transcript is not None:
                words, per_word = self._transcript_words(
                    client, headers, transcript, info.duration_s, secrets
                )
        words = fit_words(words, info.duration_s)
        if words and per_word:
            self.capabilities.timestamp_granularity = "word"
        elif self.capabilities.timestamp_granularity != "word":
            self.capabilities.timestamp_granularity = "sentence"
        timing_source = "provider" if words and per_word else ("estimated" if words else "none")
        return SynthResult(
            path=Path(request.output_path),
            duration_s=info.duration_s,
            sample_rate_hz=info.sample_rate,
            channels=info.channels,
            text=request.text,
            words=words,
            timing_source=timing_source,
            provider=self.id,
            model=self.model_for(request, voice_id),
            voice_id=voice_id,
            characters=len(request.text),
            cost_usd=0.0,
            sentence_id=request.sentence_id,
        )

    def _get_file(self, client: Any, url: str, headers: dict[str, str]) -> Any:
        """``GET`` a result file, again after a passing failure (network, 5xx, 429): the task
        is paid for, so its audio is worth another try. The key travels only to the API host
        (see :func:`download`)."""
        wait = self.poll_interval_s
        for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
            final = attempt == MAX_DOWNLOAD_ATTEMPTS
            try:
                response = download(client, url, headers=headers, trusted_url=self.base_url)
            except Exception as exc:
                if final or not is_transient_error(exc):
                    raise
                sleep(wait)
            else:
                if final or not is_transient_status(int(response.status_code)):
                    return response
                retry_after = retry_after_seconds(response) or 0.0
                sleep(max(wait, min(retry_after, MAX_POLL_WAIT_S)))
            wait = min(wait * POLL_BACKOFF, self.poll_max_interval_s)
        raise ProviderError(f"The audio file could not be downloaded from {TOOL_NAME}.")

    def _fetch_audio(
        self, client: Any, headers: dict[str, str], payload: Any, secrets: Sequence[str]
    ) -> tuple[bytes, str]:
        inline = find_inline_audio(payload)
        if inline:
            return inline, "mp3"
        audio_url = find_audio_url(payload)
        if not audio_url:
            raise ProviderError(f"{TOOL_NAME} finished the task but gave no audio link.")
        try:
            fetched = self._get_file(client, audio_url, headers)
            fetched.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            reason = server_text(explain_ai33_error(exc, secrets), secrets)
            raise ProviderError(
                f"The audio file could not be downloaded from {TOOL_NAME}: {reason}"
            ) from exc
        content_type = str(fetched.headers.get("content-type", "")).lower()
        audio_format = format_from_content_type(content_type) or format_from_url(audio_url)
        return bytes(fetched.content), audio_format or "mp3"

    def _transcript_words(
        self, client: Any, headers: dict[str, str], transcript: Any, duration_s: float,
        secrets: Sequence[str],
    ) -> tuple[list[WordTiming], bool]:
        """Timing is optional: any problem is logged and the stage estimates instead."""
        value = transcript
        try:
            if is_http_url(value):
                fetched = download(client, str(value), headers=headers, trusted_url=self.base_url)
                fetched.raise_for_status()
                content_type = str(fetched.headers.get("content-type", "")).lower()
                if "json" in content_type:
                    value = fetched.json()
                else:
                    value = fetched.text
            return words_from_transcript(value, duration_s)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ai33 transcript could not be used: %s",
                server_text(explain_ai33_error(exc, secrets), secrets),
            )
            return [], False

    def estimate_cost(self, text: str) -> CostEstimate:
        units = len(text)
        return CostEstimate(
            provider=self.id,
            unit="credit",
            units=units,
            cost_usd=0.0,
            note=f"Billed in ai33 credits ({units} characters); the dollar price depends on "
            "your ai33 plan.",
        )

    def _health(self, status: Any, detail: str, key_set: bool) -> ProviderHealth:
        return ProviderHealth(
            provider=self.id, kind="voice", status=status, detail=detail,
            key_env=self.key_env, key_set=key_set, model=self.model_for(),
        )

    def health(self) -> ProviderHealth:
        """Local facts only (is the key there?), like every adapter, so the Settings and
        Doctor pages never wait on ai33. ``options.health_probe: true`` makes this
        :meth:`check_key` instead."""
        if not key_is_set(self.key_env):
            return self._health(
                "not_configured", MISSING_KEY_MESSAGE.format(env=self.key_env), False
            )
        if self.health_probe:
            return self.check_key()
        return self._health(
            "ok",
            "Key set (ai33 checks it with the first sentence). Sentences are made through "
            "ai33 tasks and billed in credits.",
            True,
        )

    def check_key(self) -> ProviderHealth:
        """The explicit "check key" action: ``GET /v3/voices?provider=minimax&page_size=1``
        (5 s timeout) to learn whether ai33 accepts the key -> ``ok``, ``fail`` (rejected)
        or ``warn`` (ai33 unreachable). The answer is cached for a minute per key and shared
        by every instance, so repeated checks cost at most one request a minute."""
        if not key_is_set(self.key_env):
            return self.health()
        key = self._key()
        cache_key = _health_cache_key(self.key_env, self.base_url, key)
        with _HEALTH_LOCK:
            cached = _HEALTH_CACHE.get(cache_key)
            if cached is not None and monotonic() - cached[0] < HEALTH_CACHE_TTL_S:
                return cached[1].model_copy()
            result = self._ask_key(key)
            _HEALTH_CACHE[cache_key] = (monotonic(), result)
        return result.model_copy()

    def _ask_key(self, key: str) -> ProviderHealth:
        secrets = [key]
        try:
            with http_client(timeout_s=HEALTH_TIMEOUT_S) as client:
                payload = self._voices_page(
                    client, self._headers(key),
                    {"provider": "minimax", "page": 1, "page_size": 1}, secrets,
                )
        except ProviderNotConfigured as exc:
            return self._health("fail", server_text(exc, secrets), True)
        except ProviderError as exc:
            return self._health(
                "warn",
                f"Key set, but ai33 could not be checked right now: {server_text(exc, secrets)}",
                True,
            )
        total = dig(payload, "pagination.total")
        count = (
            f" ({total} MiniMax voices listed)"
            if isinstance(total, int) and not isinstance(total, bool) else ""
        )
        return self._health("ok", f"Key accepted by ai33{count}. Billed in ai33 credits.", True)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
