"""The cloud voice adapters (MiniMax, Cartesia, generic HTTP) without the network: request
bodies, answer parsing, key handling and the registry wiring. The HTTP calls run against an
httpx MockTransport; the real services are only touched by the ``live`` tests, which skip
unless a key (and a voice id) is set."""

from __future__ import annotations

import base64
import json
import os
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from cashcow_studio.providers import registry
from cashcow_studio.providers.base import ProviderError, ProviderNotConfigured
from cashcow_studio.providers.catalog import load_catalog
from cashcow_studio.providers.voice import _http, cartesia, generic_http, minimax
from cashcow_studio.providers.voice.base import ProviderCapabilities, SynthRequest, VoiceProvider
from cashcow_studio.providers.voice.cartesia import CartesiaVoiceProvider
from cashcow_studio.providers.voice.generic_http import GenericHttpVoiceProvider
from cashcow_studio.providers.voice.minimax import MinimaxVoiceProvider
from cashcow_studio.providers.voice.mock import MockVoiceProvider

FAKE_KEY = "fake-voice-key-0123456789ABCDEF"
SR = 48000


def wav_bytes(seconds: float, sample_rate: int = SR, channels: int = 1) -> bytes:
    path = Path(os.environ.get("TEMP", ".")) / f"ccs-voice-{os.getpid()}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(round(seconds * sample_rate) * 2 * channels))
    data = path.read_bytes()
    path.unlink()
    return data


def mock_client(handler: Any) -> Any:
    def factory(timeout_s: float = 10.0) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout_s)

    return factory


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MINIMAX_API_KEY", "MINIMAX_GROUP_ID", "CARTESIA_API_KEY",
                 "GENERIC_VOICE_API_KEY", "AI33_API_KEY", "CCS_VOICE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


# Shared helpers ----------------------------------------------------------------------------


def test_dig_and_decode() -> None:
    assert _http.dig({"a": {"b": [10, {"c": 3}]}}, "a.b.1.c") == 3
    assert _http.dig({"a": {"b": [10]}}, "a.b.5") is None
    assert _http.dig({"a": 1}, "") == {"a": 1}
    assert _http.decode_audio_field(base64.b64encode(b"xyz").decode()) == b"xyz"
    data_url = "data:audio/mp3;base64," + base64.b64encode(b"q").decode()
    assert _http.decode_audio_field(data_url) == b"q"
    assert _http.decode_audio_field("7879", encoding="hex") == b"xy"
    with pytest.raises(ProviderError, match="hex"):
        _http.decode_audio_field("zz", encoding="hex")
    with pytest.raises(ProviderError, match="without any audio"):
        _http.decode_audio_field("")


def test_normalise_words_handles_rows_columns_and_milliseconds() -> None:
    rows = [{"word": "Hi", "start": 100, "end": 400, "score": 0.5}, {"word": "", "start": 1},
            {"text": "there", "start": "bad", "end": 2}]
    words = _http.normalise_words(rows, time_unit="ms")
    assert len(words) == 1 and words[0].text == "Hi"
    assert words[0].start_s == pytest.approx(0.1) and words[0].end_s == pytest.approx(0.4)
    assert words[0].confidence == 0.5 and words[0].source == "provider"
    columns = {"words": ["a", "b"], "start": [0.0, 0.5], "end": [0.5, 1.0]}
    assert [w.text for w in _http.normalise_words(columns)] == ["a", "b"]
    assert _http.normalise_words(None) == []
    split = _http.split_segments_into_words(_http.normalise_words(
        [{"text": "two words", "start": 0.0, "end": 1.0}]
    ))
    assert [w.text for w in split] == ["two", "words"] and split[-1].end_s == 1.0


def test_explain_http_error_scrubs_the_key() -> None:
    request = httpx.Request("POST", "https://api.example.com/tts")
    response = httpx.Response(401, request=request, text=f"bad key {FAKE_KEY}")
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    text = _http.explain_http_error(exc, "Tool", [FAKE_KEY])
    assert FAKE_KEY not in text and "Settings > API keys" in text
    response = httpx.Response(400, request=request, text=f"bad request for {FAKE_KEY}")
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    text = _http.explain_http_error(exc, "Tool", [FAKE_KEY])
    assert "***" in text and FAKE_KEY not in text
    assert "rate-limiting" in _http.explain_http_error(
        httpx.HTTPStatusError("x", request=request, response=httpx.Response(429, request=request)),
        "Tool", [],
    )
    assert "could not be reached" in _http.explain_http_error(
        httpx.ConnectError("no route"), "Tool", []
    )


def test_deliver_audio_writes_pcm_and_wav(tmp_path: Path) -> None:
    pcm = bytes(SR * 2)  # one second of 48 kHz silence as raw samples
    out = _http.deliver_audio(pcm, tmp_path / "a.wav", audio_format="pcm_s16le",
                              sample_rate_hz=SR, source_sample_rate_hz=SR)
    with wave.open(str(out), "rb") as handle:
        assert handle.getnframes() == SR and handle.getframerate() == SR
    out = _http.deliver_audio(wav_bytes(0.5), tmp_path / "b.wav", audio_format="wav",
                              sample_rate_hz=SR)
    with wave.open(str(out), "rb") as handle:
        assert handle.getnframes() == SR // 2
    with pytest.raises(ProviderError, match="empty audio"):
        _http.deliver_audio(b"", tmp_path / "c.wav", audio_format="wav", sample_rate_hz=SR)


def test_require_key_messages_never_hold_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_VOICE_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured) as excinfo:
        _http.require_key("SOME_VOICE_KEY", "Tool")
    assert "SOME_VOICE_KEY" in str(excinfo.value)
    monkeypatch.setenv("SOME_VOICE_KEY", FAKE_KEY)
    assert _http.require_key("SOME_VOICE_KEY", "Tool") == FAKE_KEY


# MiniMax -------------------------------------------------------------------------------------


def test_minimax_imports_and_health_without_keys(no_keys: None, tmp_path: Path) -> None:
    provider = MinimaxVoiceProvider()
    assert isinstance(provider, VoiceProvider)
    assert provider.model_for() == "speech-2.8-hd"
    health = provider.health()
    assert health.status == "not_configured" and "MINIMAX_API_KEY" in health.detail
    with pytest.raises(ProviderNotConfigured, match="MINIMAX_API_KEY"):
        provider.synthesize(SynthRequest(text="Hello", output_path=tmp_path / "x.wav",
                                         voice_id="v1"))
    assert provider.estimate_cost("12345").cost_usd == pytest.approx(0.0005)


def test_minimax_body_and_subtitles() -> None:
    request = SynthRequest(text="Hi there.", output_path=Path("x.wav"), voice_id="v-1",
                           language="Spanish", speed=1.2, style="calm, warm")
    body = minimax.build_body(request, "speech-2.8-hd")
    assert body["model"] == "speech-2.8-hd" and body["text"] == "Hi there."
    assert body["voice_setting"]["voice_id"] == "v-1" and body["voice_setting"]["speed"] == 1.2
    assert body["voice_setting"]["emotion"] == "calm"
    assert body["audio_setting"] == {"sample_rate": 44100, "bitrate": 128000, "format": "wav",
                                     "channel": 1}
    assert body["subtitle_enable"] is True and body["language_boost"] == "Spanish"
    assert minimax.language_boost("Urdu") == "auto"
    words = minimax.words_from_subtitles([
        {"text": "Hi there.", "time_begin": 0, "time_end": 800},
        {"text": "Yes", "time_begin": 900, "time_end": 1200},
    ])
    assert [w.text for w in words] == ["Hi", "there.", "Yes"]
    assert words[-1].start_s == pytest.approx(0.9) and words[0].start_s == 0.0
    minimax.check_base_resp({"base_resp": {"status_code": 0}})
    with pytest.raises(ProviderNotConfigured, match="rejected the API key"):
        minimax.check_base_resp({"base_resp": {"status_code": 1004, "status_msg": "login fail"}})
    with pytest.raises(ProviderError, match="refused"):
        minimax.check_base_resp({"base_resp": {"status_code": 2013, "status_msg": "bad"}})


def test_minimax_synthesize_against_a_fake_server(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MINIMAX_GROUP_ID", "group-1")
    seen: list[httpx.Request] = []
    audio = wav_bytes(1.0, sample_rate=48000)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/t2a_v2":
            assert request.url.params["GroupId"] == "group-1"
            assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
            payload = json.loads(request.content)
            assert payload["subtitle_enable"] is True
            return httpx.Response(200, json={
                "data": {"audio": audio.hex(), "status": 2,
                         "subtitle_file": "https://files.example/sub.json"},
                "extra_info": {"usage_characters": 12, "audio_length": 1000},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })
        if request.url.path == "/sub.json":
            return httpx.Response(200, json=[
                {"text": "Hello there", "time_begin": 0, "time_end": 600},
                {"text": "friend.", "time_begin": 700, "time_end": 1000},
            ])
        return httpx.Response(404)

    monkeypatch.setattr(minimax, "http_client", mock_client(handler))
    provider = MinimaxVoiceProvider()
    assert provider.health().status == "ok"
    result = provider.synthesize(SynthRequest(
        text="Hello there friend.", output_path=tmp_path / "out.wav", voice_id="v-1",
    ))
    assert result.path == tmp_path / "out.wav" and result.duration_s == pytest.approx(1.0)
    assert result.sample_rate_hz == SR and result.provider == "minimax"
    assert [w.text for w in result.words] == ["Hello", "there", "friend."]
    assert result.timing_source == "provider" and result.characters == 12
    assert result.cost_usd == pytest.approx(12 * 0.0001)
    assert len(seen) == 2 and FAKE_KEY not in str(result.model_dump())

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"denied {FAKE_KEY}")

    monkeypatch.setattr(minimax, "http_client", mock_client(failing))
    with pytest.raises(ProviderError) as excinfo:
        provider.synthesize(SynthRequest(text="x", output_path=tmp_path / "y.wav", voice_id="v"))
    assert FAKE_KEY not in str(excinfo.value) and "API key" in str(excinfo.value)


# Cartesia ------------------------------------------------------------------------------------


def test_cartesia_imports_health_and_body(no_keys: None, tmp_path: Path) -> None:
    provider = CartesiaVoiceProvider()
    assert isinstance(provider, VoiceProvider) and provider.model_for() == "sonic-3"
    assert provider.health().status == "not_configured"
    with pytest.raises(ProviderNotConfigured, match="CARTESIA_API_KEY"):
        provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "x.wav", voice_id="v"))
    body = cartesia.build_body(
        SynthRequest(text="Hi.", output_path=Path("x"), voice_id="v-9", language="German",
                     speed=1.3), "sonic-3",
    )
    assert body["voice"] == {"mode": "id", "id": "v-9"} and body["language"] == "de"
    assert body["output_format"] == {"container": "raw", "encoding": "pcm_s16le",
                                     "sample_rate": 48000}
    assert body["add_timestamps"] is True and body["speed"] == 1.3
    older = cartesia.build_body(
        SynthRequest(text="Hi.", output_path=Path("x"), voice_id="v", speed=0.8), "sonic-2"
    )
    assert older["speed"] == "slow"
    assert "speed" not in cartesia.build_body(
        SynthRequest(text="Hi.", output_path=Path("x"), voice_id="v"), "sonic-3"
    )


def test_cartesia_sse_parsing() -> None:
    chunk = base64.b64encode(bytes(96)).decode()
    lines = [
        ": keep-alive",
        f'data: {{"type": "chunk", "data": "{chunk}", "context_id": "c1"}}',
        "",
        'data: {"type": "timestamps", "word_timestamps": {"words": ["Hi", "there"], '
        '"start": [0.0, 0.3], "end": [0.25, 0.6]}}',
        "",
        "data: not json",
        "",
        'data: {"type": "done"}',
    ]
    events = cartesia.parse_sse_lines(lines)
    assert [e["type"] for e in events] == ["chunk", "timestamps", "done"]
    pcm, words = cartesia.collect_events(events)
    assert pcm == bytes(96) and [w.text for w in words] == ["Hi", "there"]
    assert words[1].start_s == pytest.approx(0.3)
    with pytest.raises(ProviderError, match="reported an error"):
        cartesia.collect_events([{"type": "error", "error": "voice not found"}])


def test_cartesia_synthesize_against_a_fake_server(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARTESIA_API_KEY", FAKE_KEY)
    pcm = bytes(SR * 2)  # one second
    chunk = base64.b64encode(pcm).decode()
    body = "\n".join([
        f'data: {{"type": "chunk", "data": "{chunk}"}}',
        "",
        'data: {"type": "timestamps", "word_timestamps": {"words": ["Hello"], "start": [0.1], '
        '"end": [0.5]}}',
        "",
        'data: {"type": "done"}',
        "",
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tts/sse"
        assert request.headers["x-api-key"] == FAKE_KEY
        assert request.headers["cartesia-version"]
        payload = json.loads(request.content)
        assert payload["add_timestamps"] is True
        headers = {"content-type": "text/event-stream"}
        return httpx.Response(200, content=body.encode(), headers=headers)

    monkeypatch.setattr(cartesia, "http_client", mock_client(handler))
    provider = CartesiaVoiceProvider()
    result = provider.synthesize(SynthRequest(text="Hello", output_path=tmp_path / "c.wav",
                                              voice_id="v-1"))
    assert result.duration_s == pytest.approx(1.0) and result.sample_rate_hz == SR
    assert [w.text for w in result.words] == ["Hello"] and result.timing_source == "provider"
    with wave.open(str(result.path), "rb") as handle:
        assert handle.getnframes() == SR and handle.getnchannels() == 1


# Generic HTTP ----------------------------------------------------------------------------------


def recipe(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": "https://api.example.com/v1/tts",
        "method": "POST",
        "headers": {"Authorization": "Bearer ${GENERIC_VOICE_API_KEY}",
                    "Content-Type": "application/json"},
        "body_template": {"text": "{{text}}", "voice": "{{voice}}", "language": "{{language}}",
                          "speed": "{{speed}}", "note": "speed {{speed}} for {{voice}}"},
        "response": {"kind": "json", "audio_field": "data.audio", "audio_encoding": "base64",
                     "audio_format": "wav", "word_timings_path": "data.words",
                     "word_fields": {"text": "w", "start": "s", "end": "e"}, "time_unit": "ms"},
    }
    base.update(overrides)
    return base


def capabilities(**overrides: Any) -> ProviderCapabilities:
    data: dict[str, Any] = {"id": "generic_http", "name": "Example voice", "adapter": "ready",
                            "key_env": "GENERIC_VOICE_API_KEY", "http": recipe()}
    data.update(overrides)
    return ProviderCapabilities.model_validate(data)


def test_generic_placeholders_and_env(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"text": "Hi", "speed": 1.5, "voice": "v"}
    assert generic_http.fill_placeholders("{{speed}}", values) == 1.5
    assert generic_http.fill_placeholders("x {{speed}} {{voice}}", values) == "x 1.5 v"
    assert generic_http.fill_placeholders({"a": ["{{text}}", 1]}, values) == {"a": ["Hi", 1]}
    assert generic_http.fill_placeholders("{{unknown}}", values) == "{{unknown}}"
    missing: list[str] = []
    assert generic_http.expand_env("Bearer ${GENERIC_VOICE_API_KEY}", missing) == "Bearer "
    assert missing == ["GENERIC_VOICE_API_KEY"]
    assert generic_http.env_names_in(recipe()) == ["GENERIC_VOICE_API_KEY"]

    provider = GenericHttpVoiceProvider(capabilities())
    assert provider.configured and provider.health().status == "not_configured"
    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.build_request(SynthRequest(text="Hi", output_path=Path("x.wav")))
    assert "GENERIC_VOICE_API_KEY" in str(excinfo.value)

    monkeypatch.setenv("GENERIC_VOICE_API_KEY", FAKE_KEY)
    method, url, headers, body, secrets = provider.build_request(
        SynthRequest(text="Hello", output_path=Path("x.wav"), voice_id="v-2", speed=1.25,
                     language="French")
    )
    assert method == "POST" and url == "https://api.example.com/v1/tts"
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert body == {"text": "Hello", "voice": "v-2", "language": "French", "speed": 1.25,
                    "note": "speed 1.25 for v-2"}
    assert secrets == [FAKE_KEY]
    assert provider.health().status == "ok" and FAKE_KEY not in provider.health().detail


def test_generic_unconfigured_entry(no_keys: None, tmp_path: Path) -> None:
    provider = GenericHttpVoiceProvider()
    assert not provider.configured and provider.health().status == "not_configured"
    with pytest.raises(ProviderNotConfigured, match="no API address"):
        provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "x.wav"))
    with pytest.raises(ProviderNotConfigured):
        provider.create_clone("v", [], None)  # type: ignore[arg-type]
    assert provider.list_voices() == []
    # The repo catalogue ships the template unconfigured.
    catalog = load_catalog()
    assert "generic_http" in catalog.voice
    assert catalog.voice["generic_http"].http["url"] == ""
    assert catalog.voice["generic_http"].http["response"]["audio_field"] == "audio"
    assert catalog.voice["minimax"].adapter == "ready"
    assert catalog.voice["minimax"].default_model == "speech-2.8-hd"
    assert catalog.voice["cartesia"].adapter == "ready"
    assert catalog.voice["ai33"].adapter == "stub" and not catalog.voice["ai33"].http


def test_generic_synthesize_json_audio_field(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GENERIC_VOICE_API_KEY", FAKE_KEY)
    audio = base64.b64encode(wav_bytes(0.5)).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        payload = json.loads(request.content)
        assert payload["speed"] == 1.0 and payload["text"] == "Hello there"
        return httpx.Response(200, json={"data": {
            "audio": audio,
            "words": [{"w": "Hello", "s": 0, "e": 200}, {"w": "there", "s": 250, "e": 500}],
        }})

    monkeypatch.setattr(generic_http, "http_client", mock_client(handler))
    provider = GenericHttpVoiceProvider(capabilities())
    result = provider.synthesize(SynthRequest(text="Hello there", output_path=tmp_path / "g.wav",
                                              voice_id="v"))
    assert result.duration_s == pytest.approx(0.5) and result.provider == "generic_http"
    assert [w.text for w in result.words] == ["Hello", "there"]
    assert result.words[1].start_s == pytest.approx(0.25) and result.timing_source == "provider"


def test_generic_synthesize_audio_url_and_raw_body(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GENERIC_VOICE_API_KEY", FAKE_KEY)
    wav = wav_bytes(0.25)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/tts":
            return httpx.Response(200, json={"result": {"url": "https://cdn.example/clip.wav"}})
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    monkeypatch.setattr(generic_http, "http_client", mock_client(handler))
    by_url = recipe(response={"kind": "json", "audio_url_field": "result.url"})
    provider = GenericHttpVoiceProvider(capabilities(http=by_url))
    result = provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "u.wav"))
    assert result.duration_s == pytest.approx(0.25) and calls == ["/v1/tts", "/clip.wav"]
    assert result.words == [] and result.timing_source == "none"

    raw = recipe(response={"kind": "audio"})
    provider = GenericHttpVoiceProvider(capabilities(http=raw))

    def raw_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav, headers={"content-type": "audio/x-wav"})

    monkeypatch.setattr(generic_http, "http_client", mock_client(raw_handler))
    result = provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "r.wav"))
    assert result.duration_s == pytest.approx(0.25)

    def missing_field(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nothing": True})

    monkeypatch.setattr(generic_http, "http_client", mock_client(missing_field))
    provider = GenericHttpVoiceProvider(capabilities())
    with pytest.raises(ProviderError, match="data.audio"):
        provider.synthesize(SynthRequest(text="Hi", output_path=tmp_path / "m.wav"))


def test_same_origin_and_manual_redirects() -> None:
    assert _http.same_origin("https://api.example.com/v1/tts", "https://API.example.com:443/x")
    assert not _http.same_origin("https://api.example.com/a", "http://api.example.com/a")
    assert not _http.same_origin("https://api.example.com/a", "https://cdn.example.com/a")
    assert not _http.same_origin("not a url", "https://cdn.example.com/a")

    seen: list[tuple[str, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, "x-api-key" in request.headers))
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://cdn.example/file.wav"})
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        return httpx.Response(200, content=b"RIFF")

    client = mock_client(handler)()
    response = _http.download(
        client, "https://api.example.com/hop", headers={"X-API-Key": FAKE_KEY},
        trusted_url="https://api.example.com/v1/tts",
    )
    assert response.content == b"RIFF"
    assert seen == [("api.example.com", True), ("cdn.example", False)]
    with pytest.raises(ProviderError, match="redirected more than"):
        _http.download(client, "https://api.example.com/loop", max_redirects=2)


def test_generic_audio_url_on_another_host_gets_no_key_headers(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The key headers travel to the API host only: a storage or CDN link (also one reached
    through a redirect) gets a bare request, unless the recipe asks for the headers."""
    monkeypatch.setenv("GENERIC_VOICE_API_KEY", FAKE_KEY)
    wav = wav_bytes(0.25)
    target = {"url": ""}
    seen: list[tuple[str, str, bool, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((
            request.url.host, request.url.path,
            "authorization" in request.headers, "x-api-key" in request.headers,
        ))
        if request.url.path == "/v1/tts":
            return httpx.Response(200, json={"result": {"url": target["url"]}})
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://cdn.example/clip.wav"})
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    monkeypatch.setattr(generic_http, "http_client", mock_client(handler))
    headers = {"Authorization": "Bearer ${GENERIC_VOICE_API_KEY}",
               "X-API-Key": "${GENERIC_VOICE_API_KEY}", "Content-Type": "application/json"}

    def provider(**response: Any) -> GenericHttpVoiceProvider:
        spec = {"kind": "json", "audio_url_field": "result.url", **response}
        return GenericHttpVoiceProvider(capabilities(http=recipe(headers=headers, response=spec)))

    # The API's own host keeps the key on the download.
    target["url"] = "https://api.example.com/files/clip.wav"
    provider().synthesize(SynthRequest(text="Hi", output_path=tmp_path / "a.wav"))
    assert seen == [("api.example.com", "/v1/tts", True, True),
                    ("api.example.com", "/files/clip.wav", True, True)]
    # Another host (a presigned storage link) never sees it.
    seen.clear()
    target["url"] = "https://cdn.example/clip.wav?X-Amz-Signature=abc"
    provider().synthesize(SynthRequest(text="Hi", output_path=tmp_path / "b.wav"))
    assert seen == [("api.example.com", "/v1/tts", True, True),
                    ("cdn.example", "/clip.wav", False, False)]
    # A redirect from the API host to a CDN drops the headers on the way.
    seen.clear()
    target["url"] = "https://api.example.com/hop"
    provider().synthesize(SynthRequest(text="Hi", output_path=tmp_path / "c.wav"))
    assert seen == [("api.example.com", "/v1/tts", True, True),
                    ("api.example.com", "/hop", True, True),
                    ("cdn.example", "/clip.wav", False, False)]
    # Only an explicit recipe flag sends the headers to another host.
    seen.clear()
    target["url"] = "https://cdn.example/clip.wav"
    provider(audio_url_send_headers=True).synthesize(
        SynthRequest(text="Hi", output_path=tmp_path / "d.wav")
    )
    assert seen[-1] == ("cdn.example", "/clip.wav", True, True)


def test_generic_string_body_template_and_get(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GENERIC_VOICE_API_KEY", FAKE_KEY)
    wav = wav_bytes(0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.params["text"] == "Hi all"
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    monkeypatch.setattr(generic_http, "http_client", mock_client(handler))
    http = recipe(method="GET", body_template={"text": "{{text}}", "v": "{{voice}}"},
                  response={"kind": "audio", "audio_format": "wav"})
    provider = GenericHttpVoiceProvider(capabilities(http=http))
    result = provider.synthesize(SynthRequest(text="Hi all", output_path=tmp_path / "q.wav"))
    assert result.duration_s == pytest.approx(0.1)
    text_template = recipe(body_template='{"t": "{{text}}"}')
    provider = GenericHttpVoiceProvider(capabilities(http=text_template))
    _, _, _, body, _ = provider.build_request(SynthRequest(text="Yo", output_path=Path("x")))
    assert body == '{"t": "Yo"}'


# Registry wiring -------------------------------------------------------------------------------


def test_registry_builds_the_new_adapters(no_keys: None) -> None:
    assert isinstance(registry.build_voice_provider("minimax"), MinimaxVoiceProvider)
    assert isinstance(registry.build_voice_provider("cartesia"), CartesiaVoiceProvider)
    generic = registry.build_voice_provider("generic_http")
    assert isinstance(generic, GenericHttpVoiceProvider) and generic.id == "generic_http"
    assert isinstance(registry.build_voice_provider("mock"), MockVoiceProvider)
    assert {"minimax", "cartesia", "generic_http"} <= set(registry.known_providers("voice"))
    rows = {row.id: row for row in registry.list_provider_status() if row.kind == "voice"}
    assert rows["minimax"].status == "not_configured"
    assert rows["minimax"].adapter == "ready" and rows["cartesia"].adapter == "ready"
    dump = json.dumps([r.model_dump(mode="json") for r in rows.values()])
    assert "key_set" in dump


def test_ai33_is_wired_through_the_generic_adapter_when_its_recipe_is_filled(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    yaml_text = """
voice:
  ai33:
    name: ai33 voice clone
    adapter: stub
    clone: true
    key_env: AI33_API_KEY
    http:
      url: https://api.ai33.example/v1/tts
      headers: {Authorization: "Bearer ${AI33_API_KEY}"}
      body_template: {text: "{{text}}", voice: "{{voice}}"}
      response: {kind: audio, audio_format: wav}
"""
    (tmp_path / "providers.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setenv("CCS_CONFIG_DIR", str(tmp_path))
    provider = registry.build_voice_provider("ai33")
    assert isinstance(provider, GenericHttpVoiceProvider) and provider.id == "ai33"
    assert provider.capabilities.name == "ai33 voice clone"
    health = provider.health()
    assert health.status == "not_configured" and "AI33_API_KEY" in health.detail
    assert registry.wired_by_config(provider.capabilities)
    assert not registry.wired_by_config(load_catalog(tmp_path / "nope.yaml").voice["ai33"])
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "ai33")
    assert registry.build_providers()["voice"].id == "ai33"


# Live (skipped without keys) -------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("MINIMAX_API_KEY") and os.environ.get("CCS_LIVE_MINIMAX_VOICE_ID")),
    reason="MINIMAX_API_KEY and CCS_LIVE_MINIMAX_VOICE_ID are not set",
)
def test_minimax_live(tmp_path: Path) -> None:
    provider = MinimaxVoiceProvider()
    result = provider.synthesize(SynthRequest(
        text="This is a short live test.", output_path=tmp_path / "live.wav",
        voice_id=os.environ["CCS_LIVE_MINIMAX_VOICE_ID"],
    ))
    assert result.duration_s > 0.5 and result.sample_rate_hz == SR


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("CARTESIA_API_KEY") and os.environ.get("CCS_LIVE_CARTESIA_VOICE_ID")),
    reason="CARTESIA_API_KEY and CCS_LIVE_CARTESIA_VOICE_ID are not set",
)
def test_cartesia_live(tmp_path: Path) -> None:
    provider = CartesiaVoiceProvider()
    result = provider.synthesize(SynthRequest(
        text="This is a short live test.", output_path=tmp_path / "live.wav",
        voice_id=os.environ["CCS_LIVE_CARTESIA_VOICE_ID"],
    ))
    assert result.duration_s > 0.5 and result.words
