"""The ai33 (OpenSpeaker) voice adapter without the network: multipart submission, the
task-status probe and poll loop (passing failures, answers about something else, forgotten
paths), audio download without leaking the key, WAV conversion, SRT and JSON transcripts,
clone id prefixing, voices paging, error mapping with key values scrubbed from every server
text, consent, the local health report and the explicit key check, and the registry /
channel / Settings wiring. Every HTTP call runs against an httpx MockTransport."""

from __future__ import annotations

import io
import json
import re
import wave
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError

from cashcow_studio.models.channel import Channel, VoiceConfig
from cashcow_studio.pipeline.stages import voice as voice_stage
from cashcow_studio.providers import registry
from cashcow_studio.providers.base import ProviderError, ProviderNotConfigured
from cashcow_studio.providers.catalog import load_catalog
from cashcow_studio.providers.status import pipeline_provider_status
from cashcow_studio.providers.voice import _http, ai33
from cashcow_studio.providers.voice.ai33 import Ai33VoiceProvider
from cashcow_studio.providers.voice.base import (
    CloneConsent,
    SynthRequest,
    VoiceProvider,
)
from cashcow_studio.providers.voice.mock import MockVoiceProvider

FAKE_KEY = "fake-ai33-key-0123456789ABCDEF"
KEY_START = FAKE_KEY[:10]
SR = 48000
API_HOST = "api.ai33.pro"
CDN_HOST = "files.ai33-storage.example"
SRT = (
    "1\n00:00:00,000 --> 00:00:00,120\nHello there\n\n"
    "2\n00:00:00,120 --> 00:00:00,200\nfriend.\n"
)
TIMEOUT = "timeout"
"""In a scripted poll list: the request times out (httpx.ReadTimeout)."""


def wav_bytes(seconds: float, sample_rate: int = SR) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(round(seconds * sample_rate) * 2))
    return buffer.getvalue()


def mock_client(handler: Any, timeouts: list[float] | None = None) -> Any:
    def factory(timeout_s: float = 10.0) -> httpx.Client:
        if timeouts is not None:
            timeouts.append(timeout_s)
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout_s)

    return factory


def done_task(audio_host: str = API_HOST) -> dict[str, Any]:
    return {"success": True, "data": {"status": "completed",
                                      "audio_url": f"https://{audio_host}/f/a.wav"}}


def scripted(
    polls: list[Any],
    submit: dict[str, Any] | None = None,
    seen: list[str] | None = None,
    audio: bytes | None = None,
) -> Any:
    """A handler: the POST answers ``submit`` (task t1), every status request takes the next
    entry of ``polls`` (a JSON body, an httpx.Response or TIMEOUT), and any .wav/.mp3 link
    gives ``audio``."""
    answers = iter(polls)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json=submit or {"success": True, "task_id": "t1"})
        if request.url.path.endswith((".wav", ".mp3")):
            return httpx.Response(200, content=audio or wav_bytes(0.1),
                                  headers={"content-type": "audio/wav"})
        answer = next(answers)
        if answer == TIMEOUT:
            raise httpx.ReadTimeout("timed out", request=request)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    return handler


def multipart_fields(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """``{field: (filename or None, body bytes)}`` of a multipart/form-data request."""
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data"), content_type
    boundary = content_type.split("boundary=", 1)[1].strip('"').encode()
    fields: dict[str, tuple[str | None, bytes]] = {}
    for part in request.content.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head)
        filename = re.search(rb'filename="([^"]*)"', head)
        if name:
            fields[name.group(1).decode()] = (
                filename.group(1).decode() if filename else None, body.rstrip(b"\r\n"),
            )
    return fields


def text_fields(request: httpx.Request) -> dict[str, str]:
    return {k: v[1].decode("utf-8") for k, v in multipart_fields(request).items()}


def consent() -> CloneConsent:
    return CloneConsent(owner_name="Daniel", consented_by="Imran", consented_at=datetime.now(UTC))


def with_options(**options: Any) -> Ai33VoiceProvider:
    caps = ai33.default_capabilities()
    caps.options = {**caps.options, **options}
    return Ai33VoiceProvider(caps)


def request_for(tmp_path: Path, name: str = "s.wav", **fields: Any) -> SynthRequest:
    fields.setdefault("voice_id", "1")
    return SynthRequest(text=fields.pop("text", "x"), output_path=tmp_path / name, **fields)


def write_catalog(folder: Path, **options: Any) -> Path:
    """A providers.yaml whose ai33 entry carries ``options`` (the rest are defaults)."""
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"name": "ai33 (OpenSpeaker)", "adapter": "ready", "billing_unit": "credit",
             "key_env": "AI33_API_KEY", "options": options}
    path = folder / "providers.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "voice": {"ai33": entry}}),
                    encoding="utf-8")
    return path


def chain_text(exc: BaseException) -> str:
    """The exception and everything it was raised from, as one text."""
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 10:
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


@pytest.fixture(autouse=True)
def fresh_health_cache() -> Any:
    ai33.reset_health_cache()
    yield
    ai33.reset_health_cache()


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AI33_API_KEY", "CCS_VOICE_PROVIDER", "CCS_CONFIG_DIR", "MINIMAX_API_KEY",
                 "MINIMAX_GROUP_ID", "CARTESIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    registry.reset_channel_voice_providers()


@pytest.fixture
def keyed(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI33_API_KEY", FAKE_KEY)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(ai33, "sleep", waited.append)
    return waited


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A clock that moves only while the adapter waits."""
    state: dict[str, Any] = {"now": 1000.0, "sleeps": []}

    def fake_sleep(seconds: float) -> None:
        state["sleeps"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr(ai33, "monotonic", lambda: state["now"])
    monkeypatch.setattr(ai33, "sleep", fake_sleep)
    return state


# Without a key -------------------------------------------------------------------------------


def test_imports_capabilities_and_messages_without_a_key(no_keys: None, tmp_path: Path) -> None:
    provider = Ai33VoiceProvider()
    assert isinstance(provider, VoiceProvider) and provider.id == "ai33"
    caps = provider.capabilities
    assert caps.adapter == "ready" and caps.clone is True and caps.billing_unit == "credit"
    assert caps.max_chars == 1_000_000 and caps.timestamp_granularity == "sentence"
    assert caps.languages[:3] == ["English", "Spanish", "Hindi"] and "Vietnamese" in caps.languages
    assert provider.base_url == "https://api.ai33.pro" and provider.poll_paths[0].startswith("/v3/")
    health = provider.health()
    assert health.status == "not_configured" and "Add your ai33 API key" in health.detail
    assert health.key_env == "AI33_API_KEY" and health.key_set is False
    assert provider.check_key() == health  # nothing to check without a key
    with pytest.raises(ProviderNotConfigured, match="Add your ai33 API key"):
        provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "x.wav", voice_id="1"))
    with pytest.raises(ProviderNotConfigured, match="Add your ai33 API key"):
        provider.list_voices()
    estimate = provider.estimate_cost("twelve chars")
    assert estimate.unit == "credit" and estimate.units == 12 and estimate.cost_usd == 0.0
    assert "credits" in estimate.note


def test_voice_id_prefixing_speed_clamp_and_form() -> None:
    assert ai33.normalise_voice_id("123") == "clone_123"
    assert ai33.normalise_voice_id(" 77 ") == "clone_77"
    assert ai33.normalise_voice_id("clone_123") == "clone_123"
    assert ai33.normalise_voice_id("minimax_abc") == "minimax_abc"
    assert ai33.normalise_voice_id("ElevenLabs_X1") == "ElevenLabs_X1"
    assert ai33.normalise_voice_id("edge_vi-VN-HoaiMyNeural") == "edge_vi-VN-HoaiMyNeural"
    assert ai33.normalise_voice_id("https://ai33.pro/app/voices/55") == "clone_55"
    assert ai33.normalise_voice_id("abc", default_prefix="") == "abc"
    assert ai33.normalise_voice_id("abc", default_prefix="minimax") == "minimax_abc"
    assert ai33.normalise_voice_id("") == "" and ai33.normalise_voice_id("   ") == ""
    assert ai33.engine_of("fishaudio_9") == "fishaudio" and ai33.engine_of("x") == ""
    assert ai33.clamp_speed(2.0) == 1.5 and ai33.clamp_speed(0.1) == 0.5
    assert ai33.clamp_speed(1.0) == 1.0 and ai33.clamp_speed("bad") == 1.0  # type: ignore[arg-type]
    request = SynthRequest(text="Hi.", output_path=Path("x.wav"), voice_id="123", speed=2.0)
    form = ai33.build_form(request, "clone_123", "ccs-abc")
    assert form == {"text": "Hi.", "voice_id": "clone_123", "speed": "1.5",
                    "with_transcript": "true", "file_name": "ccs-abc"}
    assert ai33.multipart_fields(form)["speed"] == (None, "1.5")
    provider = Ai33VoiceProvider()
    assert provider.poll_url("/v3/tasks/{task_id}", "t 1") == "https://api.ai33.pro/v3/tasks/t%201"
    assert provider.poll_url("/common/task?task_id={task_id}", "t1").endswith("?task_id=t1")
    assert provider.poll_url("/v3/status", "t1") == "https://api.ai33.pro/v3/status/t1"
    assert provider.poll_url("https://other.example/t/{task_id}", "t1") == "https://other.example/t/t1"


# Submit -> poll -> download -> convert --------------------------------------------------------


def test_synthesize_submits_polls_downloads_and_converts(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, bool]] = []
    state = {"polls": 0}
    # A 24 kHz WAV served as audio/mpeg: FFmpeg has to resample it to the pipeline's 48 kHz.
    audio = wav_bytes(0.2, sample_rate=24000)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path, "xi-api-key" in request.headers))
        if request.url.host == API_HOST:
            assert request.headers["xi-api-key"] == FAKE_KEY
        if request.method == "POST" and request.url.path == "/v3/text-to-speech":
            fields = text_fields(request)
            assert fields["text"] == "Hello there friend."
            assert fields["voice_id"] == "clone_123" and fields["speed"] == "1.2"
            assert fields["with_transcript"] == "true"
            assert fields["file_name"].startswith("ccs-") and len(fields["file_name"]) == 28
            return httpx.Response(200, json={"success": True, "task_id": "task-1"})
        if request.url.path == "/v3/tasks/task-1":
            return httpx.Response(404, json={"success": False, "message": "Not Found"})
        if request.url.path == "/v3/task/task-1":
            state["polls"] += 1
            if state["polls"] == 1:
                return httpx.Response(200, json={"success": True, "data": {"status": "pending"}})
            return httpx.Response(200, json={"success": True, "data": {
                "status": "completed",
                "audio_url": f"https://{CDN_HOST}/out/task-1.mp3?X-Amz-Signature=abc",
                "transcript_url": f"https://{CDN_HOST}/out/task-1.srt",
            }})
        if request.url.host == CDN_HOST and request.url.path == "/out/task-1.mp3":
            assert "xi-api-key" not in request.headers
            return httpx.Response(200, content=audio, headers={"content-type": "audio/mpeg"})
        if request.url.host == CDN_HOST and request.url.path == "/out/task-1.srt":
            return httpx.Response(200, text=SRT, headers={"content-type": "text/plain"})
        return httpx.Response(500, text="unexpected " + request.url.path)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    assert provider.remembered_poll_path is None
    result = provider.synthesize(SynthRequest(
        text="Hello there friend.", output_path=tmp_path / "s1.wav", voice_id="123",
        speed=1.2, sentence_id="s1",
    ))
    assert result.path == tmp_path / "s1.wav" and result.sample_rate_hz == SR
    assert result.channels == 1 and result.duration_s == pytest.approx(0.2, abs=0.01)
    with wave.open(str(result.path), "rb") as handle:
        assert handle.getframerate() == SR and handle.getnchannels() == 1
    assert result.provider == "ai33" and result.voice_id == "clone_123"
    assert result.characters == len("Hello there friend.") and result.cost_usd == 0.0
    assert result.model == "clone" and result.sentence_id == "s1"
    # The SRT gives sentence cues: words cut by length, marked estimated -> provider_sentence.
    assert [w.text for w in result.words] == ["Hello", "there", "friend."]
    assert {w.source for w in result.words} == {"estimated"}
    assert result.words[0].start_s == 0.0 and result.words[-1].end_s == pytest.approx(0.2)
    assert result.timing_source == "estimated"
    assert provider.capabilities.timestamp_granularity == "sentence"
    # Probe order, the remembered path, one wait while pending, no key on the storage host.
    assert [path for _, path, _ in seen] == [
        "/v3/text-to-speech", "/v3/tasks/task-1", "/v3/task/task-1", "/v3/task/task-1",
        "/out/task-1.mp3", "/out/task-1.srt",
    ]
    assert [(host, keyed_) for host, _, keyed_ in seen if host == CDN_HOST] == [
        (CDN_HOST, False), (CDN_HOST, False),
    ]
    assert provider.remembered_poll_path == "/v3/task/{task_id}"
    assert sleeps == [1.5]
    assert FAKE_KEY not in json.dumps(result.model_dump(mode="json"))

    # The next sentence goes straight to the remembered path (no 404 probe).
    seen.clear()
    provider.synthesize(SynthRequest(
        text="Hello there friend.", output_path=tmp_path / "s2.wav", voice_id="clone_123",
        speed=1.2,
    ))
    assert [path for _, path, _ in seen][:2] == ["/v3/text-to-speech", "/v3/task/task-1"]
    assert "/v3/tasks/task-1" not in [path for _, path, _ in seen]


def test_poll_backoff_timeout_and_unknown_endpoint(
    keyed: None, clock: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    polls: list[str] = []

    def pending(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t2"})
        polls.append(request.url.path)
        return httpx.Response(200, json={"status": "processing"})

    # 1.5 s growing by half each time, capped at 5 s, until the 20 s timeout has passed.
    monkeypatch.setattr(ai33, "http_client", mock_client(pending))
    provider = with_options(poll_timeout_s=20)
    with pytest.raises(ProviderError, match="did not finish") as excinfo:
        provider.synthesize(SynthRequest(text="x", output_path=tmp_path / "t.wav", voice_id="1"))
    message = str(excinfo.value)
    # The task was seen running, so the advice to raise the timeout is right here.
    assert "within 20 seconds" in message and "poll_timeout_s" in message
    assert FAKE_KEY not in message
    assert clock["sleeps"] == [1.5, 2.25, 3.375, 5.0, 5.0, 5.0]
    assert polls == ["/v3/tasks/t2"] * 7  # the first candidate answered, so it was kept

    requests: list[str] = []

    def never_found(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t3"})
        requests.append(request.url.path)
        return httpx.Response(404, text="no such route")

    monkeypatch.setattr(ai33, "http_client", mock_client(never_found))
    clock["sleeps"].clear()
    provider = Ai33VoiceProvider()
    with pytest.raises(ProviderError, match="task status endpoint not found") as excinfo:
        provider.synthesize(SynthRequest(text="x", output_path=tmp_path / "u.wav", voice_id="1"))
    message = str(excinfo.value)
    assert "poll_paths" in message and "Common tab" in message and "not found (404)" in message
    assert "poll_timeout_s" not in message
    assert provider.remembered_poll_path is None
    # Every path, PROBE_ROUNDS times (a new task may not be visible at once), with waits.
    assert len(requests) == len(ai33.DEFAULT_POLL_PATHS) * ai33.PROBE_ROUNDS
    assert clock["sleeps"] == [1.5, 2.25, 3.375]

    # A custom poll path with a query string, and a 200 answer that is not JSON is skipped.
    wav = wav_bytes(0.1)
    seen: list[str] = []

    def custom(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t9"})
        if request.url.path == "/html/status":
            return httpx.Response(200, text="<html>not json</html>")
        if request.url.path == "/custom/status":
            assert request.url.params["task_id"] == "t9"
            return httpx.Response(200, json={
                "success": True, "data": {"status": "success",
                                          "result": {"audio_url": "https://api.ai33.pro/f/a.wav"}},
            })
        if request.url.path == "/f/a.wav":
            assert request.headers["xi-api-key"] == FAKE_KEY  # same host as the API: key sent
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(custom))
    provider = with_options(poll_paths=["/html/status?task_id={task_id}",
                                        "/custom/status?task_id={task_id}"])
    result = provider.synthesize(SynthRequest(text="x", output_path=tmp_path / "v.wav",
                                              voice_id="1"))
    assert result.duration_s == pytest.approx(0.1) and result.timing_source == "none"
    assert provider.remembered_poll_path == "/custom/status?task_id={task_id}"
    assert seen == ["/v3/text-to-speech", "/html/status", "/custom/status", "/f/a.wav"]


def test_json_words_become_word_timings_without_polling(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav = wav_bytes(0.2)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "status": "completed", "data": {
                "audio_url": "https://api.ai33.pro/files/a.wav",
                "transcript": {"words": [
                    {"word": "Hello", "start": 0, "end": 100, "confidence": 0.9},
                    {"word": "there", "start": 100, "end": 200},
                ]},
            }})
        if request.url.path == "/files/a.wav":
            assert request.headers["xi-api-key"] == FAKE_KEY
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    result = provider.synthesize(SynthRequest(text="Hello there", output_path=tmp_path / "w.wav",
                                              voice_id="clone_5"))
    assert seen == ["/v3/text-to-speech", "/files/a.wav"] and sleeps == []
    assert [w.text for w in result.words] == ["Hello", "there"]
    assert result.words[0].end_s == pytest.approx(0.1)  # milliseconds detected
    assert result.words[1].start_s == pytest.approx(0.1) and result.words[1].end_s == 0.2
    assert result.words[0].confidence == 0.9 and {w.source for w in result.words} == {"provider"}
    assert result.timing_source == "provider"
    assert provider.capabilities.timestamp_granularity == "word"


def test_direct_audio_answer(keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wav = wav_bytes(0.25)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    result = Ai33VoiceProvider().synthesize(
        SynthRequest(text="Hi", output_path=tmp_path / "d.wav", voice_id="vbee_x")
    )
    assert calls == ["POST"] and result.duration_s == pytest.approx(0.25)
    assert result.words == [] and result.timing_source == "none" and result.voice_id == "vbee_x"


def test_audio_without_sound_is_refused_and_a_bad_link_name_stays_a_provider_error(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A valid WAV header without samples would be cached and stay silent on every rerun.
    def silent(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "status": "completed",
                                             "audio_url": f"https://{CDN_HOST}/a.wav"})
        return httpx.Response(200, content=wav_bytes(0.0), headers={"content-type": "audio/wav"})

    monkeypatch.setattr(ai33, "http_client", mock_client(silent))
    with pytest.raises(ProviderError, match="without any sound"):
        Ai33VoiceProvider().synthesize(request_for(tmp_path, "e.wav"))
    assert not (tmp_path / "e.wav").exists()

    # "v1.2/a" has no file extension: the format falls back to mp3 instead of "2/a".
    def odd_link(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "status": "completed",
                                             "audio_url": f"https://{CDN_HOST}/v1.2/a"})
        return httpx.Response(200, content=b"ID3" + bytes(400),
                              headers={"content-type": "application/octet-stream"})

    monkeypatch.setattr(ai33, "http_client", mock_client(odd_link))
    with pytest.raises(ProviderError):
        Ai33VoiceProvider().synthesize(request_for(tmp_path, "o.wav"))


# Transcript and task helpers --------------------------------------------------------------------


def test_transcript_parsers() -> None:
    cues = ai33.parse_srt("WEBVTT\n\n00:01.000 --> 00:02.500\n<b>Hi</b> all\n\n"
                          "00:00:02,600 --> 00:00:03,000\n\n\n00:00:03,000 --> 00:00:04,000\nBye")
    assert cues == [(1.0, 2.5, "Hi all"), (3.0, 4.0, "Bye")]
    words, per_word = ai33.words_from_transcript(SRT, 0.2)
    assert [w.text for w in words] == ["Hello", "there", "friend."] and per_word is False
    assert words[1].end_s == pytest.approx(0.12) and words[1].source == "estimated"

    nested = json.dumps({"data": {"segments": [
        {"text": "Hi all", "start": 0.0, "end": 0.5,
         "words": [{"text": "Hi", "start": 0.0, "end": 0.2},
                   {"text": "all", "start": 0.2, "end": 0.5}]},
    ]}})
    words, per_word = ai33.words_from_transcript(nested, 0.5)
    assert per_word is True and [w.text for w in words] == ["Hi", "all"]
    assert words[1].start_s == pytest.approx(0.2) and words[1].source == "provider"

    segments_only = [{"text": "Hi there", "start": "00:00:00,000", "end": "00:00:01,000"},
                     {"text": "Bye", "start_time": 1.0, "end_time": 1.5}]
    words, per_word = ai33.words_from_transcript(segments_only, 1.5)
    assert per_word is False and [w.text for w in words] == ["Hi", "there", "Bye"]
    assert words[1].end_s == pytest.approx(1.0) and words[2].end_s == pytest.approx(1.5)
    assert {w.source for w in words} == {"estimated"}

    # What the voice stage makes of the two kinds: measured words -> provider_word, words cut
    # from sentence cues -> provider_sentence.
    measured, _ = ai33.words_from_transcript(nested, 0.5)
    cut, _ = ai33.words_from_transcript(SRT, 0.2)
    assert voice_stage.shift_words(measured, 0.0, 0.5, "Hi all")[1] == "provider"
    assert voice_stage.shift_words(cut, 0.0, 0.2, "Hello there friend.")[1] == "estimated"
    stage_source = voice_stage.doc_source
    assert stage_source([NS(words_source="provider")], False, False) == "provider_word"
    assert stage_source([NS(words_source="estimated")], False, False) == "provider_sentence"

    flat_words = [{"word": "a", "start": 0, "end": 300}, {"word": "b", "start": 300, "end": 900}]
    words, per_word = ai33.words_from_transcript(flat_words, 0.9)
    assert per_word is True and words[-1].end_s == pytest.approx(0.9)
    # Without the audio length, a time over ten minutes (600) can only be milliseconds.
    words, per_word = ai33.words_from_transcript({"words": [{"word": "a", "start": 0, "end": 500}]})
    assert per_word is True and words[0].end_s == 500.0  # under ten minutes: taken as seconds
    words, _ = ai33.words_from_transcript({"words": [{"word": "a", "start": 0, "end": 800}]})
    assert words[0].end_s == pytest.approx(0.8)  # 800 s would be over ten minutes
    words, _ = ai33.words_from_transcript({"words": [{"word": "a", "start": 0, "end": 80000}]})
    assert words[0].end_s == pytest.approx(80.0)
    assert ai33.words_from_transcript("plain text without timing") == ([], False)
    assert ai33.words_from_transcript("{not json") == ([], False)
    assert ai33.words_from_transcript({"something": "else"}) == ([], False)
    assert ai33.words_from_transcript(None) == ([], False)
    fitted = ai33.fit_words(words, 50.0)
    assert fitted[0].end_s == 50.0
    assert ai33.fit_words(ai33.words_from_transcript(flat_words, 0.9)[0], 0.2) == [
        ai33.words_from_transcript(flat_words, 0.9)[0][0].model_copy(update={"end_s": 0.2})
    ]


def test_task_helpers_and_audio_url_search() -> None:
    assert ai33.find_audio_url({"result": {"audio_url": "https://x/a"}}) == "https://x/a"
    assert ai33.find_audio_url({"data": {"files": [{"link": "https://x/clip.ogg"}]}}) == (
        "https://x/clip.ogg"
    )
    assert ai33.find_audio_url({"data": {"output": "https://cdn/audio/123"}}) == (
        "https://cdn/audio/123"
    )
    no_audio = {"transcript_url": "https://x/a.srt", "note": "https://x/a.json"}
    assert ai33.find_audio_url(no_audio) == ""
    assert ai33.find_audio_url({"task_id": "abc", "status": "pending"}) == ""
    # A voice preview or a help page is not the result, whatever its link looks like.
    assert ai33.find_audio_url({"voice": {"preview_url": "https://cdn/preview.mp3"}}) == ""
    assert ai33.find_audio_url({"help": "https://ai33.pro/app/audio-history"}) == ""
    assert ai33.task_status({"data": {"state": "Running"}}) == "running"
    assert ai33.task_status({"status": "In Progress"}) == "in_progress"
    # The task's own word beats the envelope's: failure > running > finished.
    assert ai33.task_status({"status": "success", "data": {"status": "processing"}}) == (
        "processing"
    )
    assert ai33.task_status({"status": "success", "data": {"status": "failed"}}) == "failed"
    assert ai33.extract_task_id({"data": {"task_id": "t"}}) == "t"
    assert ai33.extract_task_id({"id": 42}) == "42"
    assert ai33.classify_task({"status": "queued"}) == "pending"
    assert ai33.classify_task({"status": "weird"}) == "pending"
    assert ai33.classify_task({"audio_url": "https://x/a.mp3"}) == "done"
    assert ai33.classify_task({"success": True}) == "unknown"
    # A link announced while the task still runs is where the file will be, not the file.
    assert ai33.classify_task({"status": "processing", "audio_url": "https://x/a.mp3"}) == (
        "pending"
    )
    assert ai33.classify_task({"status": "completed", "audio_url": "https://x/a.mp3"}) == "done"
    with pytest.raises(ProviderError, match="could not make this sentence: Voice not found"):
        ai33.classify_task({"status": "failed", "error": {"message": "Voice not found"}})
    for word in ("fail", "errored", "rejected", "expired"):
        with pytest.raises(ProviderError, match="could not make this sentence: nope"):
            ai33.classify_task({"status": word, "message": "nope"})
    with pytest.raises(ProviderError, match="finished but gave no audio"):
        ai33.classify_task({"status": "completed"})
    # The answer to the POST: "success" there means accepted, so a task id is followed.
    assert ai33.submit_outcome({"success": True, "status": "success", "task_id": "t1"}) == "poll"
    assert ai33.submit_outcome({"task_id": "t1", "status": "queued",
                                "audio_url": "https://x/a.mp3"}) == "poll"
    assert ai33.submit_outcome({"status": "completed", "audio_url": "https://x/a.mp3"}) == "done"
    with pytest.raises(ProviderError, match="no task id to follow"):
        ai33.submit_outcome({"status": "queued"})
    ai33.check_success({"success": True})
    ai33.check_success(["not", "a", "dict"])
    with pytest.raises(ProviderNotConfigured, match="rejected the key"):
        ai33.check_success({"success": False, "message": "Invalid API key"})
    with pytest.raises(ProviderError, match="credits"):
        ai33.check_success({"success": False, "error": "Insufficient credits"})
    with pytest.raises(ProviderError, match="refused the request: boom"):
        ai33.check_success({"success": False, "detail": "boom"})
    assert ai33.find_transcript({"data": {"srt_url": "https://x/t.srt"}}) == "https://x/t.srt"
    assert ai33.find_transcript({"with_transcript": True, "data": {"srt": SRT}}) == SRT
    assert ai33.find_transcript({"status": "done"}) is None
    assert ai33.format_from_url("https://x/y/clip.M4A?sig=1") == "m4a"
    assert ai33.format_from_url("https://x/v1.2/a") == ""  # no extension: never "2/a"
    assert ai33.format_from_url("https://x/a.") == "" and ai33.format_from_url("https://x/") == ""
    assert ai33.format_from_content_type("audio/mpeg; charset=binary") == "mp3"
    info = ai33.voice_info_from_row(
        {"voice_id": "9", "name": "Own", "language": "en", "gender": "Male",
         "tags": ["warm", 3], "preview_url": "https://p/9.mp3"}, "clone",
    )
    assert info is not None and info.id == "clone_9" and info.is_clone and info.gender == "Male"
    assert info.tags == ["warm", "3"] and info.description == "warm, 3"
    assert ai33.voice_info_from_row({"name": "no id"}) is None
    assert ai33.voice_info_from_row("junk") is None


def test_what_counts_as_an_answer_about_the_task() -> None:
    about = ai33.describes_task
    assert about({"status": "processing"}, "t1")
    assert about({"success": True, "data": {"state": "completed"}}, "t1")
    assert about({"id": "t1"}, "t1") and about({"data": {"task": {"uuid": "t1"}}}, "t1")
    assert about({"success": False, "task_id": "t1", "message": "Task not found"}, "t1")
    assert about({"result": {"audio_url": "https://cdn/x?sig=1"}}, "t1")  # under an audio key
    assert about({"file": "https://cdn/out/t1.mp3"}, "t1")  # an audio file extension
    # Not about this task: a route that answers anything, an envelope refusal, another id.
    assert not about({"message": "Cannot GET /v3/tasks/t1"}, "t1")
    assert not about({"success": True, "data": None}, "t1")
    assert not about({"success": False, "status": "error", "message": "Task not found"}, "t1")
    assert not about({"status": "ok"}, "t1")
    assert not about({"url": "https://api.ai33.pro/v3/tasks/t1"}, "t1")
    assert not about({"task_id": "t2", "message": "other"}, "t1")
    assert not about([], "t1") and not about("t1", "t1")
    assert ai33.task_answer([{"task_id": "t0"}, {"task_id": "t1", "status": "done"}], "t1") == {
        "task_id": "t1", "status": "done",
    }
    assert ai33.task_answer([{"status": "processing"}], "t1") is None
    assert ai33.is_transient_status(503) and ai33.is_transient_status(429)
    assert not ai33.is_transient_status(404) and not ai33.is_transient_status(501)
    assert ai33.is_transient_error(httpx.ConnectError("refused"))
    assert ai33.is_transient_error(httpx.ReadTimeout("slow"))
    assert not ai33.is_transient_error(ValueError("bug"))
    assert ai33.retry_after_seconds(httpx.Response(429, headers={"retry-after": "7"})) == 7.0
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    waited = ai33.retry_after_seconds(httpx.Response(429, headers={"retry-after": soon}))
    assert waited is not None and 20 < waited <= 30
    assert ai33.retry_after_seconds(httpx.Response(429, headers={"retry-after": "soon"})) is None
    assert ai33.retry_after_seconds(httpx.Response(429)) is None


# Key values never reach a message -------------------------------------------------------------


def test_key_echoed_in_a_200_refusal_is_scrubbed_everywhere(
    keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False,
                                         "message": f"Invalid API key: {FAKE_KEY}"})

    monkeypatch.setattr(ai33, "http_client", mock_client(echo))
    provider = Ai33VoiceProvider()
    texts: list[str] = []
    with pytest.raises(ProviderNotConfigured,
                       match=r"rejected the key: Invalid API key: \*\*\*") as excinfo:
        provider.synthesize(request_for(tmp_path))
    texts.append(chain_text(excinfo.value))
    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.list_voices()
    texts.append(chain_text(excinfo.value))
    sample = tmp_path / "sample.wav"
    sample.write_bytes(wav_bytes(0.1))
    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.create_clone("Daniel", [sample], consent())
    texts.append(chain_text(excinfo.value))
    checked = provider.check_key()
    assert checked.status == "fail" and "rejected the key: Invalid API key: ***" in checked.detail
    texts.append(checked.detail)

    # What the Settings page shows with ai33 chosen and the key check switched on.
    write_catalog(tmp_path / "config", health_probe=True)
    monkeypatch.setenv("CCS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "ai33")
    ai33.reset_health_cache()
    rows = registry.list_provider_status()
    voice_row = next(row for row in pipeline_provider_status() if row.kind == "voice")
    assert voice_row.id == "ai33" and voice_row.status == "error"
    assert "rejected the key" in voice_row.detail
    texts += [json.dumps([row.model_dump(mode="json") for row in rows]), voice_row.detail]
    for text in texts:
        assert FAKE_KEY not in text and KEY_START not in text


@pytest.mark.parametrize(
    "reason",
    [f"header xi-api-key={FAKE_KEY} has no plan", "x" * 290 + FAKE_KEY + " and more"],
    ids=["inside", "across-the-300-character-cut"],
)
def test_key_echoed_in_a_failed_task_reason_is_scrubbed(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    reason: str,
) -> None:
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "failed", "error": reason}]
    )))
    with pytest.raises(ProviderError, match="could not make this sentence") as excinfo:
        Ai33VoiceProvider().synthesize(request_for(tmp_path))
    text = chain_text(excinfo.value)
    assert FAKE_KEY not in text and KEY_START not in text and "***" in text


def test_http_error_body_is_scrubbed_before_it_is_cut(
    keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = "x" * 290 + FAKE_KEY  # the key sits across the 300-character cut

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=body)

    monkeypatch.setattr(ai33, "http_client", mock_client(failing))
    with pytest.raises(ProviderError, match=r"refused the request \(400\)") as excinfo:
        Ai33VoiceProvider().synthesize(request_for(tmp_path))
    assert KEY_START not in str(excinfo.value) and "***" in str(excinfo.value)
    error = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "https://a"), response=httpx.Response(400, text=body)
    )
    message = _http.explain_http_error(error, "tool", [FAKE_KEY])
    assert KEY_START not in message and message.endswith("x***")


def test_last_guard_replaces_a_key_that_slipped_into_an_error() -> None:
    def leaks() -> None:
        raise ProviderNotConfigured(f"bad key {FAKE_KEY}")

    with pytest.raises(ProviderNotConfigured) as excinfo:
        ai33.call_without_secrets([FAKE_KEY], leaks)
    assert str(excinfo.value) == "bad key ***"
    assert excinfo.value.__cause__ is None and excinfo.value.__context__ is None

    def leaks_through_its_cause() -> None:
        try:
            raise ValueError(f"inner {FAKE_KEY}")
        except ValueError as exc:
            raise ProviderError("outer") from exc

    with pytest.raises(ProviderError) as excinfo:
        ai33.call_without_secrets([FAKE_KEY], leaks_through_its_cause)
    assert str(excinfo.value) == "outer" and FAKE_KEY not in chain_text(excinfo.value)

    clean = ProviderError("nothing secret")

    def honest() -> None:
        raise clean

    with pytest.raises(ProviderError) as excinfo:
        ai33.call_without_secrets([FAKE_KEY], honest)
    assert excinfo.value is clean
    assert ai33.call_without_secrets([FAKE_KEY], lambda: 42) == 42


# Errors -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "headers", "exc_type", "needle"),
    [
        (401, {}, ProviderNotConfigured, "rejected the key (401)"),
        (403, {}, ProviderNotConfigured, "rejected the key (403)"),
        (402, {}, ProviderError, "no credits left"),
        (429, {"retry-after": "30"}, ProviderError, "Try again in 30 seconds"),
        (429, {}, ProviderError, "rate-limiting"),
        (503, {}, ProviderError, "server problem (503)"),
        (400, {}, ProviderError, "refused the request (400)"),
    ],
)
def test_http_errors_are_plain_and_scrubbed(
    keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    status: int, headers: dict[str, str], exc_type: type[Exception], needle: str,
) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f"denied for {FAKE_KEY}", headers=headers)

    monkeypatch.setattr(ai33, "http_client", mock_client(failing))
    with pytest.raises(exc_type) as excinfo:
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "e.wav", voice_id="1")
        )
    message = str(excinfo.value)
    assert needle in message and FAKE_KEY not in message


def test_task_and_answer_errors(keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch,
                                tmp_path: Path) -> None:
    def failed_task(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t4"})
        return httpx.Response(200, json={"status": "failed", "message": "Voice not found"})

    monkeypatch.setattr(ai33, "http_client", mock_client(failed_task))
    with pytest.raises(ProviderError, match="could not make this sentence: Voice not found"):
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "f.wav", voice_id="1")
        )

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "Insufficient credits"})

    monkeypatch.setattr(ai33, "http_client", mock_client(refused))
    with pytest.raises(ProviderError, match="credits"):
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "g.wav", voice_id="1")
        )

    def no_task(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(ai33, "http_client", mock_client(no_task))
    with pytest.raises(ProviderError, match="neither a task id nor an audio link"):
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "h.wav", voice_id="1")
        )

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})

    monkeypatch.setattr(ai33, "http_client", mock_client(not_json))
    with pytest.raises(ProviderError, match="neither audio nor JSON"):
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "i.wav", voice_id="1")
        )

    def rejected_while_polling(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t5"})
        return httpx.Response(403, text="forbidden")

    monkeypatch.setattr(ai33, "http_client", mock_client(rejected_while_polling))
    with pytest.raises(ProviderNotConfigured, match="rejected the key"):
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "j.wav", voice_id="1")
        )

    # A task that was seen running and then is no longer reported: a few answers in a row
    # that say nothing about it end the sentence with the server's words (not at the first
    # one, which may be a hiccup, and not only at the timeout).
    sleeps.clear()
    answers = [{"success": True, "data": {"status": "pending"}}]
    answers += [{"success": False, "message": "Task not found"}] * ai33.MAX_UNUSABLE_ANSWERS
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        answers, submit={"success": True, "task_id": "t6"}
    )))
    with pytest.raises(ProviderError, match=r"stopped reporting on this sentence \(task t6\)"
                       r".*Task not found") as excinfo:
        Ai33VoiceProvider().synthesize(
            SynthRequest(text="x", output_path=tmp_path / "m.wav", voice_id="1")
        )
    assert "poll_timeout_s" not in str(excinfo.value)
    assert sleeps == [1.5, 2.25, 3.375, 5.0]

    with pytest.raises(ProviderError, match="no ai33 voice id"):
        Ai33VoiceProvider().synthesize(SynthRequest(text="x", output_path=tmp_path / "k.wav"))

    # A broken transcript never loses the audio.
    wav = wav_bytes(0.1)

    def bad_transcript(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={
                "success": True, "status": "completed",
                "audio_url": f"https://{CDN_HOST}/a.wav",
                "transcript_url": f"https://{CDN_HOST}/missing.srt",
            })
        if request.url.path == "/a.wav":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(bad_transcript))
    result = Ai33VoiceProvider().synthesize(
        SynthRequest(text="x", output_path=tmp_path / "l.wav", voice_id="1")
    )
    assert result.duration_s == pytest.approx(0.1) and result.timing_source == "none"


# Polling through passing trouble ----------------------------------------------------------------


def test_polling_rides_out_passing_failures(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The task is paid for once the POST is accepted: a 502, a timeout, a 429 (after its
    Retry-After) or a 503 while polling is waited out instead of dropping the sentence."""
    seen: list[str] = []
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted([
        {"status": "processing"},  # the probe: this path is about the task
        httpx.Response(502, text="Bad gateway"),
        TIMEOUT,
        httpx.Response(429, text="slow down", headers={"retry-after": "7"}),
        httpx.Response(503, text="maintenance"),
        {"status": "processing"},
        done_task(),
    ], seen=seen)))
    provider = Ai33VoiceProvider()
    result = provider.synthesize(request_for(tmp_path))
    assert result.duration_s == pytest.approx(0.1)
    assert seen.count("/v3/text-to-speech") == 1  # submitted (and paid) once
    assert seen.count("/v3/tasks/t1") == 7
    assert provider.remembered_poll_path == "/v3/tasks/{task_id}"
    assert sleeps == [1.5, 2.25, 3.375, 7.0, 5.0, 5.0]  # the 429 asked for 7 s


def test_polling_gives_up_after_passing_failures_in_a_row(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failures = [httpx.Response(503, text=f"down for {FAKE_KEY}")] * ai33.MAX_POLL_FAILURES
    seen: list[str] = []
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "processing"}, *failures], seen=seen,
    )))
    with pytest.raises(ProviderError, match=r"server problem \(503\)") as excinfo:
        Ai33VoiceProvider().synthesize(request_for(tmp_path))
    assert FAKE_KEY not in chain_text(excinfo.value)
    assert seen.count("/v3/tasks/t1") == 1 + ai33.MAX_POLL_FAILURES
    assert len(sleeps) == ai33.MAX_POLL_FAILURES


@pytest.mark.parametrize(
    ("failure", "exc_type", "needle"),
    [
        (httpx.Response(401, text="bad key"), ProviderNotConfigured, "rejected the key (401)"),
        (httpx.Response(403, text="no plan"), ProviderNotConfigured, "rejected the key (403)"),
        (httpx.Response(402, text="pay"), ProviderError, "no credits left"),
        ({"success": False, "message": f"API key expired: {FAKE_KEY}"}, ProviderNotConfigured,
         "rejected the key: API key expired: ***"),
        ({"success": False, "error": "Insufficient credits"}, ProviderError, "credits problem"),
    ],
    ids=["401", "403", "402", "key-refusal", "credits-refusal"],
)
def test_key_and_credit_problems_while_polling_stop_at_once(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    failure: Any, exc_type: type[Exception], needle: str,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "processing"}, failure], seen=seen,
    )))
    with pytest.raises(exc_type) as excinfo:
        Ai33VoiceProvider().synthesize(request_for(tmp_path))
    assert needle in str(excinfo.value) and FAKE_KEY not in str(excinfo.value)
    assert seen.count("/v3/tasks/t1") == 2 and sleeps == [1.5]


def test_audio_download_is_tried_again_after_a_passing_failure(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloads: list[str] = []
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "status": "completed",
                                             "audio_url": f"https://{CDN_HOST}/a.wav"})
        downloads.append(request.url.path)
        if len(downloads) == 1:
            return httpx.Response(503, text="busy")
        if len(downloads) == 2:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    result = Ai33VoiceProvider().synthesize(request_for(tmp_path))
    assert result.duration_s == pytest.approx(0.1) and len(downloads) == 3
    assert sleeps == [1.5, 2.25]

    # A missing file is not a passing failure: one request, then a plain error.
    downloads.clear()

    def missing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "status": "completed",
                                             "audio_url": f"https://{CDN_HOST}/gone.wav"})
        downloads.append(request.url.path)
        return httpx.Response(404, text="NoSuchKey")

    monkeypatch.setattr(ai33, "http_client", mock_client(missing))
    with pytest.raises(ProviderError, match="could not be downloaded"):
        Ai33VoiceProvider().synthesize(request_for(tmp_path, "g.wav"))
    assert downloads == ["/gone.wav"]


# Finding the status path ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrong_answer",
    [
        {"message": "Cannot GET /v3/tasks/t1"},
        {"success": True, "data": None},
        [],
        [{"status": "processing"}],
        {"success": False, "status": "error", "message": "Task not found"},
        {"url": "https://api.ai33.pro/v3/tasks/t1"},
        {"status": "ok", "docs": "https://ai33.pro/app/audio-history"},
    ],
    ids=["message", "data-null", "empty-list", "list-without-the-id", "envelope-refusal",
         "url-that-is-no-audio", "status-ok-with-help-link"],
)
def test_probe_keeps_only_a_path_that_answers_about_the_task(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    wrong_answer: Any,
) -> None:
    seen: list[str] = []
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        if request.url.path == "/v3/tasks/t1":  # not the task route, but answers JSON
            return httpx.Response(200, json=wrong_answer)
        if request.url.path == "/v3/task/t1":  # the real route
            return httpx.Response(200, json=done_task())
        if request.url.path == "/f/a.wav":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    result = provider.synthesize(request_for(tmp_path))
    assert result.duration_s == pytest.approx(0.1)
    assert provider.remembered_poll_path == "/v3/task/{task_id}"
    assert seen == ["/v3/text-to-speech", "/v3/tasks/t1", "/v3/task/t1", "/f/a.wav"]
    assert sleeps == []


def test_a_refusing_candidate_only_rules_out_that_path(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The POST was just accepted with this key, so a 403 from one candidate (a web route
    that wants a login) means "not this path"; only every candidate refusing is the key."""
    seen: list[str] = []
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        if request.url.path == "/v3/tasks/t1":
            return httpx.Response(403, text="login required")
        if request.url.path == "/v3/task/t1":
            return httpx.Response(200, json={"success": False,
                                             "message": "Unauthorized: web session needed"})
        if request.url.path == "/v3/common/task/t1":
            return httpx.Response(200, json=done_task())
        if request.url.path == "/f/a.wav":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    assert provider.synthesize(request_for(tmp_path)).duration_s == pytest.approx(0.1)
    assert provider.remembered_poll_path == "/v3/common/task/{task_id}" and sleeps == []

    def everywhere(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        return httpx.Response(401, text="revoked")

    seen.clear()
    monkeypatch.setattr(ai33, "http_client", mock_client(everywhere))
    with pytest.raises(ProviderNotConfigured, match=r"rejected the key \(401\)"):
        Ai33VoiceProvider().synthesize(request_for(tmp_path, "b.wav"))
    assert len(seen) == 1 + len(ai33.DEFAULT_POLL_PATHS) and sleeps == []


def test_probe_asks_every_path_again_while_the_task_is_not_visible_yet(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []
    state = {"n": 0}
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        if request.url.path == "/v3/tasks/t1":
            state["n"] += 1
            if state["n"] == 1:  # the queue has not caught up yet
                return httpx.Response(404, json={"success": False, "message": "Task not found"})
            if state["n"] == 2:
                return httpx.Response(200, json={"status": "processing"})
            return httpx.Response(200, json=done_task())
        if request.url.path == "/f/a.wav":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    result = provider.synthesize(request_for(tmp_path))
    assert result.duration_s == pytest.approx(0.1)
    assert provider.remembered_poll_path == "/v3/tasks/{task_id}"
    assert seen.count("/v3/tasks/t1") == 3
    assert len(seen) == 1 + len(ai33.DEFAULT_POLL_PATHS) + 2 + 1
    assert sleeps == [1.5, 2.25]


def test_a_refusal_without_a_status_is_never_taken_for_the_task(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``{"success": false, "message": ...}`` is not about the task, while probing and on the
    remembered path alike: never remembered, never an instant failure, and the server's
    words end up in the message."""
    running = {"success": False, "message": "Task is still processing"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        if request.url.path == "/v3/task/t1":
            return httpx.Response(200, json=running)
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    with pytest.raises(ProviderError, match="task status endpoint not found") as excinfo:
        provider.synthesize(request_for(tmp_path))
    assert "/v3/task/{task_id}: an answer without a task status: Task is still processing" in (
        str(excinfo.value)
    )
    assert provider.remembered_poll_path is None
    assert len(sleeps) == ai33.PROBE_ROUNDS - 1

    sleeps.clear()
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "processing"}] + [running] * ai33.MAX_UNUSABLE_ANSWERS
    )))
    provider = Ai33VoiceProvider()
    with pytest.raises(ProviderError, match="stopped reporting.*Task is still processing"):
        provider.synthesize(request_for(tmp_path, "b.wav"))
    assert len(sleeps) == ai33.MAX_UNUSABLE_ANSWERS
    assert provider.remembered_poll_path == "/v3/tasks/{task_id}"  # it did describe the task


def test_a_remembered_path_that_stops_answering_about_tasks_is_forgotten(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []
    state = {"moved": False}
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "task_id": "t1"})
        if request.url.path == "/v3/tasks/t1":
            if state["moved"]:
                return httpx.Response(200, json={"message": "This route has moved"})
            return httpx.Response(200, json=done_task())
        if request.url.path == "/v3/task/t1":
            return httpx.Response(200, json=done_task())
        if request.url.path == "/f/a.wav":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = Ai33VoiceProvider()
    provider.synthesize(request_for(tmp_path, "a.wav"))
    assert provider.remembered_poll_path == "/v3/tasks/{task_id}"

    state["moved"] = True
    seen.clear()
    result = provider.synthesize(request_for(tmp_path, "b.wav"))
    assert result.duration_s == pytest.approx(0.1)
    assert provider.remembered_poll_path == "/v3/task/{task_id}"
    stale = ["/v3/tasks/t1"] * ai33.MAX_UNUSABLE_ANSWERS
    assert seen == ["/v3/text-to-speech", *stale, "/v3/tasks/t1", "/v3/task/t1", "/f/a.wav"]
    assert sleeps == [1.5, 2.25, 3.375, 5.0]


def test_only_a_task_seen_running_gets_the_timeout_advice(
    keyed: None, clock: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every path has server trouble until the deadline: that is what the message says.
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [httpx.Response(503, text="maintenance")] * 50
    )))
    with pytest.raises(ProviderError, match=r"server problem \(503\)") as excinfo:
        with_options(poll_timeout_s=3).synthesize(request_for(tmp_path))
    assert "poll_timeout_s" not in str(excinfo.value)

    # No path ever answered about the task before the deadline: the endpoint is unknown.
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [httpx.Response(404, text="no route")] * 50
    )))
    with pytest.raises(ProviderError, match="task status endpoint not found") as excinfo:
        with_options(poll_timeout_s=2).synthesize(request_for(tmp_path, "b.wav"))
    assert "poll_timeout_s" not in str(excinfo.value)
    assert "not found (404)" in str(excinfo.value)


def test_status_words_envelopes_and_announced_links(
    keyed: None, sleeps: list[float], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # "fail" is a failure word too: the server's reason at once, not a timeout.
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "fail", "message": "Voice not found"}]
    )))
    with pytest.raises(ProviderError, match="could not make this sentence: Voice not found"):
        Ai33VoiceProvider().synthesize(request_for(tmp_path))
    assert sleeps == []

    # An envelope "success" around a running task keeps polling.
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "success", "data": {"status": "processing"}}, done_task()]
    )))
    assert Ai33VoiceProvider().synthesize(request_for(tmp_path, "b.wav")).duration_s > 0
    assert sleeps == [1.5]

    # A POST answer {"success": true, "status": "success", "task_id": ...} is followed.
    seen: list[str] = []
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [done_task()], submit={"success": True, "status": "success", "task_id": "t1"},
        seen=seen,
    )))
    Ai33VoiceProvider().synthesize(request_for(tmp_path, "c.wav"))
    assert seen == ["/v3/text-to-speech", "/v3/tasks/t1", "/f/a.wav"]

    # A link announced while queued is downloaded only once the task is finished.
    seen.clear()
    link = f"https://{CDN_HOST}/out/t1.wav"
    monkeypatch.setattr(ai33, "http_client", mock_client(scripted(
        [{"status": "processing"}, {"status": "completed", "audio_url": link}],
        submit={"success": True, "task_id": "t1", "status": "queued", "audio_url": link},
        seen=seen,
    )))
    Ai33VoiceProvider().synthesize(request_for(tmp_path, "d.wav"))
    assert seen == ["/v3/text-to-speech", "/v3/tasks/t1", "/v3/tasks/t1", "/out/t1.wav"]


# Voices -------------------------------------------------------------------------------------------


def test_list_voices_pages_maps_and_caches(keyed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, str]] = []

    def row(voice_id: str, name: str, **extra: Any) -> dict[str, Any]:
        return {"voice_id": voice_id, "name": name, "language": "en-US", "gender": "Female",
                "tags": ["calm"], "preview_url": f"https://p/{voice_id}.mp3", **extra}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/voices" and request.headers["xi-api-key"] == FAKE_KEY
        params = dict(request.url.params)
        requests.append(params)
        assert params["page_size"] == "100"
        provider_name, page = params["provider"], int(params["page"])
        if provider_name == "clone":
            if page == 1:
                data = [row("clone_7", "Daniel", gender="Male", tags=["warm"]), row("9", "Own")]
                return httpx.Response(200, json={"success": True, "data": data,
                                                 "pagination": {"page": 1, "has_more": True}})
            return httpx.Response(200, json={"success": True, "data": [row("clone_8", "Mum")],
                                             "pagination": {"page": 2, "has_more": False}})
        if provider_name == "edge":
            return httpx.Response(200, json={"success": True,
                                             "data": [row("edge_vi-VN-HoaiMyNeural", "HoaiMy")],
                                             "pagination": {"has_more": False}})
        return httpx.Response(200, json={"success": True,
                                         "data": [row(f"{provider_name}_{page}", "x")],
                                         "pagination": {"has_more": True}})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    provider = with_options(voice_providers=["clone", "edge"])
    voices = provider.list_voices()
    assert [v.id for v in voices] == ["clone_7", "clone_9", "clone_8", "edge_vi-VN-HoaiMyNeural"]
    assert [v.is_clone for v in voices] == [True, True, True, False]
    assert voices[0].gender == "Male" and voices[0].tags == ["warm"] and voices[0].name == "Daniel"
    assert voices[0].preview_url == "https://p/clone_7.mp3" and voices[1].language == "en-US"
    assert len(requests) == 3 and "language" not in requests[0]
    assert provider.list_voices() == voices and len(requests) == 3  # cached for ten minutes
    by_language = provider.list_voices(language="vi-VN")
    assert len(requests) == 6 and requests[3]["language"] == "vi-VN" and len(by_language) == 4
    # Ten pages at most when a provider keeps saying has_more.
    requests.clear()
    endless = with_options(voice_providers=["minimax"])
    assert len(endless.list_voices()) == 10 and len(requests) == 10
    # The cache expires.
    endless.capabilities.options["voice_cache_ttl_s"] = 0
    endless.list_voices()
    assert len(requests) == 20


# Cloning ------------------------------------------------------------------------------------------


def test_create_clone_needs_consent_then_uploads(
    keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = Ai33VoiceProvider()
    sample = tmp_path / "sample.wav"
    sample.write_bytes(wav_bytes(0.2))
    with pytest.raises(ProviderError, match="consent"):
        provider.create_clone("Daniel", [sample], None)  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="at least one sample"):
        provider.create_clone("Daniel", [], consent())
    with pytest.raises(ProviderError, match="does not exist"):
        provider.create_clone("Daniel", [tmp_path / "nope.wav"], consent())

    uploads: list[dict[str, tuple[str | None, bytes]]] = []
    counter = {"n": 122}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v3/text-to-speech/voice-clone"
        assert request.headers["xi-api-key"] == FAKE_KEY
        fields = multipart_fields(request)
        uploads.append(fields)
        counter["n"] += 1
        return httpx.Response(200, json={"success": True, "data": {"voice_id": str(counter["n"])}})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    info = provider.create_clone("Daniel", [sample], consent())
    assert info.id == "clone_123" and info.is_clone and info.name == "Daniel"
    assert "Imran" in info.description and "Daniel" in info.description
    assert uploads[0]["voice_name"] == (None, b"Daniel")
    assert uploads[0]["audio_file"] == ("sample.wav", wav_bytes(0.2))

    # Other formats (or files over 10 MB) are converted to MP3 first; the temp file is removed.
    converted: list[tuple[Path, Path]] = []

    def fake_convert(source: Path, dest: Path, **_: Any) -> Path:
        converted.append((Path(source), Path(dest)))
        Path(dest).write_bytes(b"ID3fake-mp3")
        return Path(dest)

    monkeypatch.setattr(ai33, "convert_to_mp3", fake_convert)
    flac = tmp_path / "sample.flac"
    flac.write_bytes(b"fLaC not really")
    info = provider.create_clone("  Mum  ", [flac], consent())
    assert info.id == "clone_124" and info.name == "Mum"
    assert converted and converted[0][0] == flac and not converted[0][1].exists()
    assert uploads[1]["audio_file"] == ("sample.mp3", b"ID3fake-mp3")

    def too_big(source: Path, dest: Path, **_: Any) -> Path:
        Path(dest).write_bytes(b"0" * (ai33.MAX_CLONE_SAMPLE_BYTES + 1))
        return Path(dest)

    monkeypatch.setattr(ai33, "convert_to_mp3", too_big)
    with pytest.raises(ProviderError, match="too long"):
        provider.create_clone("Big", [flac], consent())

    def no_id(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(ai33, "http_client", mock_client(no_id))
    with pytest.raises(ProviderError, match="did not return a voice id"):
        provider.create_clone("Daniel", [sample], consent())


def test_convert_to_mp3_with_the_real_ffmpeg(tmp_path: Path) -> None:
    from cashcow_studio.audio.ffmpeg import convert_to_mp3, tools_available

    if not tools_available():
        pytest.skip("FFmpeg is not installed")
    source = tmp_path / "s.wav"
    source.write_bytes(wav_bytes(0.2))
    out = convert_to_mp3(source, tmp_path / "s.mp3")
    assert out.is_file() and out.stat().st_size > 0 and not (tmp_path / "s.mp3.part.mp3").exists()
    upload, temp = ai33.prepare_clone_sample(source)
    assert upload == source and temp is None


# Health and the key check --------------------------------------------------------------------


def test_health_is_local_and_check_key_asks_ai33_once_a_minute(
    keyed: None, clock: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, str]] = []
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        assert request.url.path == "/v3/voices" and request.headers["xi-api-key"] == FAKE_KEY
        return httpx.Response(200, json={"success": True, "data": [],
                                         "pagination": {"page": 1, "page_size": 1, "total": 318,
                                                        "has_more": True}})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler, timeouts))
    provider = Ai33VoiceProvider()
    health = provider.health()  # local facts only, like every adapter
    assert health.status == "ok" and health.key_set is True and "Key set" in health.detail
    assert calls == []

    checked = provider.check_key()  # the explicit action
    assert checked.status == "ok" and "318" in checked.detail and FAKE_KEY not in checked.detail
    assert calls == [{"provider": "minimax", "page": "1", "page_size": "1"}]
    assert timeouts == [ai33.HEALTH_TIMEOUT_S]
    # Shared by every instance (the Settings page builds a new adapter on each load).
    assert Ai33VoiceProvider().check_key() == checked and len(calls) == 1
    assert with_options(health_probe=True).health() == checked and len(calls) == 1
    clock["now"] += ai33.HEALTH_CACHE_TTL_S + 1
    Ai33VoiceProvider().check_key()
    assert len(calls) == 2
    monkeypatch.setenv("AI33_API_KEY", FAKE_KEY[::-1])  # another key is checked on its own

    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    monkeypatch.setattr(ai33, "http_client", mock_client(rejected))
    failed = Ai33VoiceProvider().check_key()
    assert failed.status == "fail" and "rejected the key" in failed.detail

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(ai33, "http_client", mock_client(unreachable))
    ai33.reset_health_cache()
    warned = Ai33VoiceProvider().check_key()
    assert warned.status == "warn" and "could not be checked" in warned.detail


def test_settings_status_never_calls_ai33_unless_switched_on(
    keyed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"success": True, "data": [],
                                         "pagination": {"total": 3}})

    monkeypatch.setattr(ai33, "http_client", mock_client(handler))
    registry.list_provider_status()
    registry.list_provider_status()
    pipeline_provider_status()  # what GET and PUT /api/settings build
    assert calls == []

    # health_probe switched on in the catalogue: at most one request a minute per key, even
    # though every call builds new adapters.
    write_catalog(tmp_path, health_probe=True)
    monkeypatch.setenv("CCS_CONFIG_DIR", str(tmp_path))
    rows = registry.list_provider_status()
    registry.list_provider_status()
    pipeline_provider_status()
    assert calls == ["/v3/voices"]
    row = next(row for row in rows if row.kind == "voice" and row.id == "ai33")
    assert row.status == "ok" and "Key accepted by ai33" in row.detail


# Registry, catalogue and channel wiring -----------------------------------------------------------


def test_registry_catalog_and_channel_wiring(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = load_catalog()
    entry = catalog.voice["ai33"]
    assert entry.adapter == "ready" and entry.billing_unit == "credit"
    assert entry.key_env == "AI33_API_KEY" and entry.base_url == "https://api.ai33.pro"
    assert entry.options["poll_paths"] == ai33.DEFAULT_POLL_PATHS
    assert entry.options["voice_providers"] == ai33.DEFAULT_VOICE_PROVIDERS
    assert entry.options["health_probe"] is False
    assert entry.languages == ai33.LANGUAGES and entry.max_chars == 1_000_000

    built = registry.build_voice_provider("ai33")
    assert isinstance(built, Ai33VoiceProvider) and built.capabilities.name == "ai33 (OpenSpeaker)"
    assert built.poll_timeout_s == 180.0 and built.poll_interval_s == 1.5
    assert built.health_probe is False
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "ai33")
    assert isinstance(registry.build_providers()["voice"], Ai33VoiceProvider)
    monkeypatch.delenv("CCS_VOICE_PROVIDER")

    # The channel model accepts the tool and the front end mirrors it.
    channel = Channel.model_validate({
        "slug": "kind-ledger", "channel": {"name": "Kind Ledger"},
        "voice": {"tool": "ai33", "clone_ref": "123", "api_key_env": "AI33_API_KEY"},
    })
    assert channel.voice.tool == "ai33"
    with pytest.raises(ValidationError):
        VoiceConfig(tool="nope")  # type: ignore[arg-type]
    channel_ts = (Path(__file__).resolve().parents[1] / "frontend/src/types/channel.ts").read_text(
        encoding="utf-8"
    )
    assert 'export const VOICE_TOOLS = [\n  "ai33",' in channel_ts

    # A channel on ai33 gets the adapter when nothing names an app-wide provider.
    mock = MockVoiceProvider()
    chosen = registry.voice_provider_for_channel(mock, "ai33")
    assert isinstance(chosen, Ai33VoiceProvider)
    assert registry.voice_provider_for_channel(mock, "ai33") is chosen  # built once
    assert registry.voice_provider_for_channel(mock, "other") is mock
    assert registry.voice_provider_for_channel(mock, "fish_audio") is mock
    assert registry.voice_provider_for_channel(mock, "") is mock
    # Only ai33 (the documented case): other tools with an adapter keep the offline mock, so
    # a channel saved earlier with MiniMax or Cartesia never starts paid calls by itself.
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test-value-0000")
    monkeypatch.setenv("CARTESIA_API_KEY", "cartesia-test-value-0000")
    assert registry.voice_provider_for_channel(mock, "minimax") is mock
    assert registry.voice_provider_for_channel(mock, "cartesia") is mock
    minimax_channel = channel.model_copy(update={
        "voice": channel.voice.model_copy(update={"tool": "minimax"}),
    })
    minimax_ctx = NS(channel=minimax_channel, settings=None, providers={"voice": mock})
    assert voice_stage._provider(minimax_ctx) is mock  # type: ignore[arg-type]
    fake = NS(id="fakecloud")
    assert registry.voice_provider_for_channel(fake, "ai33") is fake  # not the default mock
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "mock")
    assert registry.voice_provider_for_channel(mock, "ai33") is mock  # an explicit choice wins
    monkeypatch.delenv("CCS_VOICE_PROVIDER")
    ctx = NS(channel=channel, settings=None, providers={"voice": mock})
    assert isinstance(voice_stage._provider(ctx), Ai33VoiceProvider)  # type: ignore[arg-type]
    registry.reset_channel_voice_providers()
    assert registry.voice_provider_for_channel(mock, "ai33") is not chosen


def test_settings_voice_row_says_that_ai33_channels_use_ai33(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = "Channels whose Voice tab picks ai33 (OpenSpeaker) use that tool instead."
    voice_row = next(row for row in pipeline_provider_status() if row.kind == "voice")
    assert voice_row.id == "mock" and voice_row.status == "mock" and note in voice_row.detail
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "mock")  # chosen on purpose: every channel mocks
    voice_row = next(row for row in pipeline_provider_status() if row.kind == "voice")
    assert voice_row.id == "mock" and "Voice tab" not in voice_row.detail


def test_options_from_the_catalogue_file(no_keys: None, tmp_path: Path) -> None:
    path = write_catalog(
        tmp_path, poll_paths="/a/{task_id}, /b/{task_id}", poll_interval_s="bad",
        poll_max_interval_s=1e9, poll_timeout_s=float("inf"), voice_providers=[],
        default_voice_prefix="minimax", health_probe="false",
    )
    assert ".inf" in path.read_text(encoding="utf-8")
    provider = Ai33VoiceProvider(load_catalog(path).voice["ai33"])
    assert provider.poll_paths == ["/a/{task_id}", "/b/{task_id}"]
    assert provider.poll_interval_s == 1.5
    assert provider.poll_max_interval_s == ai33.MAX_POLL_WAIT_S
    assert provider.poll_timeout_s == 180.0  # .inf would keep a sentence polling for ever
    assert provider.voice_providers == ai33.DEFAULT_VOICE_PROVIDERS
    assert provider.default_voice_prefix == "minimax" and provider.health_probe is False
    assert ai33.normalise_voice_id("abc", provider.default_voice_prefix) == "minimax_abc"
    assert with_options(poll_timeout_s=10**9).poll_timeout_s == ai33.MAX_POLL_TIMEOUT_S
    assert with_options(poll_interval_s=float("nan")).poll_interval_s == 1.5
    assert with_options(health_probe="yes").health_probe is True
