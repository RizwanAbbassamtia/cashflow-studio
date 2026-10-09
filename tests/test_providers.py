"""Provider interfaces: the offline mocks, the registry defaults, the ai33 stub, the catalogue
and the Gemini adapter importing without credentials. Nothing here touches the network."""

from __future__ import annotations

import importlib
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image
from pydantic import ValidationError

from cashcow_studio.providers import ProviderError, ProviderNotConfigured, registry
from cashcow_studio.providers.catalog import config_file, load_catalog
from cashcow_studio.providers.image.base import (
    ImageProvider,
    ImageRequest,
    pixel_size,
    size_label,
)
from cashcow_studio.providers.image.gemini import (
    GeminiImageProvider,
    compose_prompt,
    explain_api_error,
    first_image_part,
)
from cashcow_studio.providers.image.mock import MockImageProvider
from cashcow_studio.providers.voice.ai33 import AI33_PENDING, Ai33VoiceProvider
from cashcow_studio.providers.voice.base import (
    CloneConsent,
    SynthRequest,
    VoiceProvider,
    estimate_speech_seconds,
)
from cashcow_studio.providers.voice.mock import MockVoiceProvider

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TEN_WORDS = "one two three four five six seven eight nine ten"
FAKE_SECRET = "fake-gemini-key-0123456789ABCDEF"


@pytest.fixture
def no_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CCS_VOICE_PROVIDER", "CCS_IMAGE_PROVIDER", "GEMINI_API_KEY", "AI33_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def consent() -> CloneConsent:
    return CloneConsent(owner_name="Imran", consented_by="Imran", consented_at=datetime.now(UTC))


# Size maths ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("aspect", "size", "expected"),
    [
        ("16:9", "2K", (2048, 1152)),
        ("9:16", "2K", (1152, 2048)),
        ("1:1", "1K", (1024, 1024)),
        ("4:3", "4K", (4096, 3072)),
        ("21:9", "2K", (2048, 878)),
        ("4:5", "1K", (818, 1024)),
        ("16:9", "1920x1080", (1920, 1080)),
        ("9:16", "1920x1080", (1080, 1920)),
        ("16:9", "2k", (2048, 1152)),
    ],
)
def test_pixel_size(aspect: str, size: str, expected: tuple[int, int]) -> None:
    assert pixel_size(aspect, size) == expected


def test_pixel_size_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="not an image size"):
        pixel_size("16:9", "huge")
    with pytest.raises(ValueError, match="not an aspect ratio"):
        pixel_size("wide", "2K")


def test_size_label() -> None:
    assert size_label("1920x1080") == "2K"
    assert size_label("1024x576") == "1K"
    assert size_label("3840x2160") == "4K"
    assert size_label("4k") == "4K"


def test_image_request_validates_aspect_and_size(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ImageRequest(prompt="x", output_path=tmp_path / "x.png", size="huge")
    with pytest.raises(ValidationError):
        ImageRequest(prompt="x", output_path=tmp_path / "x.png", aspect="wide")
    request = ImageRequest(prompt="x", output_path=tmp_path / "x.png", aspect="9:16", size="1K")
    assert request.pixels == (576, 1024)


# Mock voice ---------------------------------------------------------------------------------


def test_mock_voice_writes_silent_wav_with_even_timings(tmp_path: Path) -> None:
    provider = MockVoiceProvider()
    request = SynthRequest(text=TEN_WORDS, output_path=tmp_path / "s1.wav", sentence_id="s1")
    result = provider.synthesize(request)

    assert result.duration_s == pytest.approx(4.0)  # 10 words at 150 wpm
    with wave.open(str(result.path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 48000
        assert handle.getnframes() == 192000
        assert handle.readframes(handle.getnframes()) == bytes(192000 * 2)

    words = result.words
    assert [word.text for word in words] == TEN_WORDS.split()
    assert words[0].start_s == 0.0
    assert words[2].start_s == pytest.approx(0.8)
    assert words[2].end_s == pytest.approx(1.2)
    assert words[-1].end_s == result.duration_s
    for first, second in zip(words, words[1:], strict=False):
        assert first.end_s == pytest.approx(second.start_s)
    assert result.timing_source == "estimated"
    assert all(word.source == "estimated" for word in words)
    assert result.provider == "mock"
    assert result.cost_usd == 0.0
    assert result.characters == len(TEN_WORDS)
    assert result.sentence_id == "s1"
    assert result.sample_rate_hz == 48000


def test_mock_voice_speed_and_rate_change_duration(tmp_path: Path) -> None:
    provider = MockVoiceProvider()
    fast = provider.synthesize(
        SynthRequest(text=TEN_WORDS, output_path=tmp_path / "f.wav", speed=2.0)
    )
    slow = provider.synthesize(
        SynthRequest(text=TEN_WORDS, output_path=tmp_path / "s.wav", speaking_rate_wpm=100)
    )
    assert fast.duration_s == pytest.approx(2.0)
    assert slow.duration_s == pytest.approx(6.0)
    assert estimate_speech_seconds(" ".join(["word"] * 150)) == pytest.approx(60.0)
    assert estimate_speech_seconds("   ") == 0.0


def test_mock_voice_blank_text_raises(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="nothing to say"):
        MockVoiceProvider().synthesize(SynthRequest(text="   ", output_path=tmp_path / "b.wav"))


def test_mock_voice_other_calls(tmp_path: Path) -> None:
    provider = MockVoiceProvider()
    assert isinstance(provider, VoiceProvider)
    assert provider.list_voices()
    clone = provider.create_clone("Imran narrator", [tmp_path / "a.wav"], consent())
    assert clone.is_clone and clone.name == "Imran narrator"
    assert provider.estimate_cost("hello").cost_usd == 0.0
    assert provider.health().status == "ok"
    first = SynthRequest(text="Hello there", output_path=tmp_path / "1.wav")
    second = SynthRequest(text="Hello there", output_path=tmp_path / "2.wav")
    other = SynthRequest(text="Goodbye", output_path=tmp_path / "1.wav")
    assert first.cache_key("mock") == second.cache_key("mock")
    assert first.cache_key("mock") != other.cache_key("mock")


# Mock images --------------------------------------------------------------------------------


def test_mock_image_renders_png_in_requested_size(tmp_path: Path) -> None:
    provider = MockImageProvider()
    assert isinstance(provider, ImageProvider)
    request = ImageRequest(
        prompt="A calm mountain lake at sunrise, soft light, mist over the water",
        output_path=tmp_path / "scene_01.png",
        aspect="9:16",
        size="1K",
        negative_prompt="text, logos",
        style="watercolour",
        scene_id="1",
    )
    result = provider.generate(request)
    assert (result.width, result.height) == (576, 1024)
    data = result.path.read_bytes()
    assert data[:8] == PNG_MAGIC
    with Image.open(result.path) as image:
        assert image.size == (576, 1024)
        assert image.text.get("Description") == request.prompt
    assert result.provenance.c2pa is False and result.provenance.synthid is False
    assert result.cost_usd == 0.0
    assert result.model == "mock-placeholder"
    assert result.seed is not None
    assert result.scene_id == "1"

    again = provider.generate(request.model_copy(update={"output_path": tmp_path / "again.png"}))
    assert again.path.read_bytes() == data  # deterministic


def test_mock_image_seed_and_explicit_size(tmp_path: Path) -> None:
    provider = MockImageProvider()
    result = provider.generate(
        ImageRequest(prompt="x", output_path=tmp_path / "a.png", size="1920x1080", seed=7)
    )
    assert (result.width, result.height) == (1920, 1080)
    assert result.seed == 7
    assert provider.estimate_cost(5).cost_usd == 0.0
    assert provider.health().status == "ok"


# Registry -----------------------------------------------------------------------------------


def test_registry_defaults_to_mock(no_provider_env: None) -> None:
    providers = registry.build_providers(None)
    assert set(providers) == {"image", "voice"}
    assert isinstance(providers["image"], MockImageProvider)
    assert isinstance(providers["voice"], MockVoiceProvider)
    assert isinstance(providers["image"], ImageProvider)
    assert isinstance(providers["voice"], VoiceProvider)


def test_registry_reads_env(no_provider_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCS_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "AI33")
    providers = registry.build_providers()
    assert isinstance(providers["image"], GeminiImageProvider)
    assert isinstance(providers["voice"], Ai33VoiceProvider)
    # An explicit override wins over the environment.
    assert isinstance(registry.build_providers(image="mock")["image"], MockImageProvider)


def test_registry_uses_settings_attribute(no_provider_env: None) -> None:
    fake = NS(image_provider="gemini", voice_provider="")
    assert registry.provider_choice("image", fake) == "gemini"
    assert registry.provider_choice("voice", fake) == "mock"
    assert registry.provider_choice("voice", None) == "mock"


def test_registry_unknown_provider_does_not_raise(
    no_provider_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CCS_IMAGE_PROVIDER", "nope")
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "inworld")  # listed, adapter not built yet
    providers = registry.build_providers()
    image, voice = providers["image"], providers["voice"]
    assert isinstance(image, ImageProvider) and isinstance(voice, VoiceProvider)

    health = image.health()
    assert health.status == "fail"
    assert "nope" in health.detail and "mock" in health.detail
    with pytest.raises(ProviderNotConfigured):
        image.generate(ImageRequest(prompt="x", output_path=tmp_path / "x.png"))

    assert voice.health().status == "planned"
    assert voice.capabilities.name == "Inworld TTS"
    with pytest.raises(ProviderNotConfigured, match="later version"):
        voice.synthesize(SynthRequest(text="Hello", output_path=tmp_path / "v.wav"))


# ai33 stub ----------------------------------------------------------------------------------


def test_ai33_stub_message(no_provider_env: None, tmp_path: Path) -> None:
    provider = registry.build_voice_provider("ai33")
    assert isinstance(provider, Ai33VoiceProvider)
    assert isinstance(provider, VoiceProvider)

    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.synthesize(SynthRequest(text="Hello there", output_path=tmp_path / "a.wav"))
    assert str(excinfo.value).startswith("ai33: API details pending")
    assert str(excinfo.value) == AI33_PENDING
    with pytest.raises(ProviderNotConfigured):
        provider.list_voices()
    with pytest.raises(ProviderNotConfigured):
        provider.estimate_cost("x")
    with pytest.raises(ProviderNotConfigured):
        provider.create_clone("v", [], consent())

    caps = provider.capabilities  # from config/providers.yaml
    assert caps.id == "ai33" and caps.adapter == "stub" and caps.clone is True
    assert caps.key_env == "AI33_API_KEY"
    health = provider.health()
    assert health.status == "not_configured"
    assert health.key_set is False
    assert health.detail == AI33_PENDING


# Gemini adapter -----------------------------------------------------------------------------


def test_gemini_imports_without_credentials(no_provider_env: None, tmp_path: Path) -> None:
    module = importlib.import_module("cashcow_studio.providers.image.gemini")
    provider = module.GeminiImageProvider()
    assert provider.model == "gemini-nano-banana-2.1"
    assert isinstance(provider, ImageProvider)

    health = provider.health()
    assert health.status == "not_configured"
    assert "GEMINI_API_KEY" in health.detail
    assert health.key_set is False

    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "g.png"))
    assert "GEMINI_API_KEY" in str(excinfo.value)
    assert provider.estimate_cost(10, "2K").cost_usd == pytest.approx(0.504)
    assert provider.estimate_cost(1, "1920x1080").cost_usd == pytest.approx(0.0504)


def test_gemini_rejects_unsupported_aspect(no_provider_env: None, tmp_path: Path) -> None:
    provider = GeminiImageProvider()
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "g.png", aspect="7:3"))
    assert not isinstance(excinfo.value, ProviderNotConfigured)
    assert "7:3" in str(excinfo.value)


def test_gemini_health_ok_with_key(no_provider_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_SECRET)
    health = GeminiImageProvider().health()
    assert health.status == "ok" and health.key_set is True
    assert FAKE_SECRET not in health.model_dump_json()


def test_gemini_error_messages_never_contain_the_key() -> None:
    class FakeError(Exception):
        def __init__(self, code: int | None, message: str) -> None:
            super().__init__(message)
            self.code = code
            self.message = message

    text = explain_api_error(
        FakeError(400, f"bad request for key {FAKE_SECRET}"), "m", "GEMINI_API_KEY", FAKE_SECRET
    )
    assert FAKE_SECRET not in text and "***" in text
    assert "'m'" in explain_api_error(FakeError(404, "not found"), "m", "GEMINI_API_KEY", None)
    assert "Settings > API keys" in explain_api_error(
        FakeError(403, "forbidden"), "m", "GEMINI_API_KEY", None
    )
    assert "rate-limiting" in explain_api_error(FakeError(429, "slow"), "m", "GEMINI_API_KEY", None)
    assert "Try again" in explain_api_error(FakeError(503, "down"), "m", "GEMINI_API_KEY", None)
    assert explain_api_error(FakeError(None, ""), "m", "GEMINI_API_KEY", None).endswith(".")


def test_gemini_response_parsing() -> None:
    blob = NS(data=b"png-bytes", mime_type="image/png")
    parts = [NS(inline_data=None, text="Here it is"), NS(inline_data=blob, text=None)]
    response = NS(
        prompt_feedback=None,
        candidates=[NS(finish_reason=NS(name="STOP"), content=NS(parts=parts))],
    )
    assert first_image_part(response) == (b"png-bytes", "image/png")

    blocked = NS(prompt_feedback=NS(block_reason=NS(name="PROHIBITED_CONTENT")), candidates=[])
    with pytest.raises(ProviderError, match="declined"):
        first_image_part(blocked)

    empty_parts = [NS(inline_data=None, text="I cannot draw that.")]
    empty = NS(
        prompt_feedback=None,
        candidates=[NS(finish_reason=NS(name="NO_IMAGE"), content=NS(parts=empty_parts))],
    )
    with pytest.raises(ProviderError) as excinfo:
        first_image_part(empty)
    assert "no image" in str(excinfo.value) and "I cannot draw that." in str(excinfo.value)


def test_gemini_compose_prompt(tmp_path: Path) -> None:
    request = ImageRequest(
        prompt="A lighthouse at dusk",
        output_path=tmp_path / "x.png",
        style="oil painting",
        negative_prompt="text, people",
    )
    assert compose_prompt(request) == (
        "A lighthouse at dusk\n\nStyle: oil painting\n\nDo not include: text, people"
    )


# Catalogue ----------------------------------------------------------------------------------


def test_catalog_loads_repo_yaml() -> None:
    assert config_file().is_file()
    catalog = load_catalog()
    assert catalog.source.endswith("providers.yaml")
    assert {"mock", "ai33", "minimax"} <= set(catalog.voice)
    assert {"mock", "gemini"} <= set(catalog.image)
    gemini = catalog.image["gemini"]
    assert gemini.default_model == "gemini-nano-banana-2.1"
    assert gemini.price_by_size_usd["2K"] == pytest.approx(0.0504)
    assert gemini.provenance.synthid is True
    assert gemini.max_reference_images == 14
    assert catalog.voice["minimax"].adapter == "ready"
    assert catalog.voice["inworld"].adapter == "planned"
    assert catalog.voice["ai33"].adapter == "stub"


def test_catalog_falls_back_without_file(tmp_path: Path) -> None:
    catalog = load_catalog(tmp_path / "missing.yaml")
    assert catalog.source == "built-in"
    assert set(catalog.voice) == {"mock", "ai33"}
    assert set(catalog.image) == {"mock", "gemini"}


def test_catalog_skips_broken_entries_and_overrides_good_ones(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(
        "image:\n"
        "  gemini: {default_model: broken, aspects: [nonsense]}\n"
        "  mock: oops\n"
        "voice:\n"
        "  ai33: {name: ai33 tool, clone: true, key_env: AI33_API_KEY}\n",
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    assert catalog.source == str(path)
    assert catalog.image["gemini"].default_model == "gemini-nano-banana-2.1"  # kept built-in
    assert catalog.image["mock"].name == "Mock images (offline)"
    assert catalog.voice["ai33"].name == "ai33 tool"
    assert catalog.voice["ai33"].id == "ai33"


def test_catalog_config_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCS_CONFIG_DIR", str(tmp_path))
    assert config_file() == tmp_path / "providers.yaml"
    assert load_catalog().source == "built-in"  # no file there yet


# Status for the Settings and Doctor pages ---------------------------------------------------


def test_list_provider_status_never_leaks_keys(
    no_provider_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_SECRET)
    rows = registry.list_provider_status()
    dump = json.dumps([row.model_dump(mode="json") for row in rows])
    assert FAKE_SECRET not in dump

    by_id = {(row.kind, row.id): row for row in rows}
    assert by_id[("image", "gemini")].key_set is True
    assert by_id[("image", "gemini")].status == "ok"
    assert by_id[("image", "mock")].status == "ok"
    assert by_id[("voice", "ai33")].status == "not_configured"
    assert by_id[("voice", "minimax")].status == "not_configured"
    assert by_id[("voice", "inworld")].status == "planned"
    assert by_id[("voice", "inworld")].capabilities["timestamp_granularity"] == "word"
    selected = sorted((row.kind, row.id) for row in rows if row.selected)
    assert selected == [("image", "mock"), ("voice", "mock")]


def test_list_provider_status_flags_unknown_selection(
    no_provider_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCS_VOICE_PROVIDER", "nope")
    rows = registry.list_provider_status()
    row = next(row for row in rows if row.kind == "voice" and row.selected)
    assert row.id == "nope" and row.status == "fail"
    assert "nope" in row.detail
