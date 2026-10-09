"""Image providers for the images stage: the mock's seed-driven variety and sizes, the Gemini
adapter's request shape against a fake SDK client (no network), the model choice from
config/images.yaml, C2PA detection, and a live smoke test when GEMINI_API_KEY is set."""

from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from PIL import Image

from cashcow_studio.images.config import clear_cache, images_config
from cashcow_studio.images.phash import hamming, phash
from cashcow_studio.providers.base import ProviderError
from cashcow_studio.providers.image.base import ImageRequest
from cashcow_studio.providers.image.gemini import (
    GeminiImageProvider,
    configured_model,
    detect_c2pa,
)
from cashcow_studio.providers.image.mock import MockImageProvider

FAKE_SECRET = "fake-gemini-key-0123456789ABCDEF"


def tiny_png(
    colour: tuple[int, int, int] = (12, 200, 90), size: tuple[int, int] = (64, 36)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


# Mock ---------------------------------------------------------------------------------------------


def test_mock_seeds_change_the_picture_enough_for_the_hash(tmp_path: Path) -> None:
    provider = MockImageProvider()
    prompt = "A diner at dawn, wide shot, empty parking lot"
    first = provider.generate(ImageRequest(prompt=prompt, output_path=tmp_path / "a.png",
                                           size="1K", seed=11))
    second = provider.generate(ImageRequest(prompt=prompt, output_path=tmp_path / "b.png",
                                            size="1K", seed=12))
    same = provider.generate(ImageRequest(prompt=prompt, output_path=tmp_path / "c.png",
                                          size="1K", seed=11))
    assert hamming(phash(first.path), phash(second.path)) > 6
    assert hamming(phash(first.path), phash(same.path)) == 0
    assert same.path.read_bytes() == first.path.read_bytes()
    assert len(provider.calls) == 3 and provider.calls[0].seed == 11
    assert first.model == "mock-placeholder"
    named = provider.generate(ImageRequest(prompt=prompt, output_path=tmp_path / "d.png",
                                           size="1K", model="mock-v2"))
    assert named.model == "mock-v2"


def test_largest_size_per_provider() -> None:
    assert MockImageProvider().capabilities.largest_size() == "2K"
    assert MockImageProvider().capabilities.largest_size("1K") == "1K"
    gemini = GeminiImageProvider().capabilities
    assert gemini.largest_size() == "4K" and gemini.largest_size("2K") == "2K"
    assert gemini.model_copy(update={"sizes": []}).largest_size() == "2K"
    assert gemini.model_copy(update={"sizes": ["4K"]}).largest_size("1K") == "4K"


# Gemini against a fake client ---------------------------------------------------------------------


class FakeModels:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.sent: list[dict[str, Any]] = []

    def generate_content(self, **params: Any) -> Any:
        self.sent.append(params)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def image_response(data: bytes, mime: str = "image/png", model_version: str | None = None) -> Any:
    blob = NS(data=data, mime_type=mime)
    parts = [NS(inline_data=None, text="Here you go"), NS(inline_data=blob, text=None)]
    return NS(
        prompt_feedback=None,
        candidates=[NS(finish_reason=NS(name="STOP"), content=NS(parts=parts))],
        model_version=model_version,
    )


def fake_provider(
    monkeypatch: pytest.MonkeyPatch, answers: list[Any]
) -> tuple[GeminiImageProvider, FakeModels]:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_SECRET)
    provider = GeminiImageProvider()
    provider.retry_pause_s = 0.0
    models = FakeModels(answers)
    provider._client = NS(models=models)
    provider._client_secret = FAKE_SECRET
    return provider, models


def test_gemini_request_shape_and_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider, models = fake_provider(
        monkeypatch, [image_response(tiny_png(), model_version="nb-2.1")]
    )
    reference = tmp_path / "style_sheet.png"
    reference.write_bytes(tiny_png((1, 2, 3)))
    request = ImageRequest(
        prompt="A diner at dawn", negative_prompt="text, logos", aspect="9:16", size="1K",
        reference_images=[reference], seed=42, output_path=tmp_path / "scene_01.png",
        scene_id="1",
    )
    result = provider.generate(request)
    assert result.path == tmp_path / "scene_01.png" and result.path.is_file()
    assert (result.width, result.height) == (64, 36)
    assert result.model == "nb-2.1" and result.provider == "gemini" and result.seed == 42
    assert result.provenance.synthid is True and result.provenance.c2pa is False
    assert result.cost_usd == pytest.approx(0.0336)
    assert result.prompt_used == "A diner at dawn\n\nDo not include: text, logos"
    assert result.scene_id == "1"

    sent = models.sent[0]
    assert sent["model"] == "gemini-nano-banana-2.1"
    config = sent["config"]
    assert config.response_modalities == ["IMAGE"] and config.seed == 42
    assert config.image_config.aspect_ratio == "9:16" and config.image_config.image_size == "1K"
    contents = sent["contents"]
    assert len(contents) == 2 and contents[1] == result.prompt_used
    assert contents[0].inline_data.mime_type == "image/png"
    assert bytes(contents[0].inline_data.data) == reference.read_bytes()


def test_gemini_honours_the_request_model_and_converts_jpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 27), (9, 9, 9)).save(buffer, "JPEG")
    provider, models = fake_provider(monkeypatch, [image_response(buffer.getvalue(), "image/jpeg")])
    result = provider.generate(ImageRequest(
        prompt="x", output_path=tmp_path / "s.png", size="2K", model="gemini-3-pro-image",
    ))
    assert models.sent[0]["model"] == "gemini-3-pro-image" and result.model == "gemini-3-pro-image"
    with Image.open(result.path) as image:
        assert image.format == "PNG" and image.size == (48, 27)
    assert models.sent[0]["config"].seed is None


def test_gemini_retries_once_on_rate_limit_then_explains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from google.genai import errors

    def api_error(code: int) -> Exception:
        return errors.APIError(code, {"error": {"message": f"status {code} for {FAKE_SECRET}",
                                                "status": "x"}})

    provider, models = fake_provider(monkeypatch, [api_error(429), image_response(tiny_png())])
    result = provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "a.png", size="1K"))
    assert result.path.is_file() and len(models.sent) == 2

    provider, models = fake_provider(monkeypatch, [api_error(503), api_error(503)])
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "b.png", size="1K"))
    assert "Try again" in str(excinfo.value) and FAKE_SECRET not in str(excinfo.value)
    assert len(models.sent) == 2

    provider, models = fake_provider(monkeypatch, [api_error(400)])
    with pytest.raises(ProviderError) as excinfo:
        provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "c.png", size="1K"))
    assert len(models.sent) == 1 and FAKE_SECRET not in str(excinfo.value)
    assert "***" in str(excinfo.value)

    provider, models = fake_provider(monkeypatch, [RuntimeError(f"socket closed {FAKE_SECRET}")])
    with pytest.raises(ProviderError, match="could not be reached") as excinfo:
        provider.generate(ImageRequest(prompt="x", output_path=tmp_path / "d.png", size="1K"))
    assert FAKE_SECRET not in str(excinfo.value)


def test_gemini_model_comes_from_images_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clear_cache()
    assert images_config().model_for("gemini") == "gemini-nano-banana-2.1"
    assert configured_model() == "gemini-nano-banana-2.1"
    assert GeminiImageProvider().model == "gemini-nano-banana-2.1"
    assert GeminiImageProvider(model="custom-image-model").model == "custom-image-model"
    (tmp_path / "images.yaml").write_text("models:\n  gemini: from-yaml-model\n", encoding="utf-8")
    monkeypatch.setenv("CCS_CONFIG_DIR", str(tmp_path))
    clear_cache()
    try:
        assert configured_model() == "from-yaml-model"
        assert GeminiImageProvider().model == "from-yaml-model"
    finally:
        clear_cache()


def test_images_yaml_defaults_and_size_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from cashcow_studio.images.config import size_choice

    clear_cache()
    config = images_config()
    assert config.size == "largest" and config.max_size_label == "4K"
    assert config.suffix == "No text, no logos, no watermarks."
    assert "{reason}" in config.retry_note and "{note}" in config.note_prefix
    assert config.reference_style_sheet and config.reference_previous_scene
    assert config.qa_enabled and config.reference_extensions[0] == ".png"
    monkeypatch.delenv("CCS_IMAGE_SIZE", raising=False)
    assert size_choice(config) == "largest"
    monkeypatch.setenv("CCS_IMAGE_SIZE", "1K")
    assert size_choice(config) == "1K"


def test_detect_c2pa() -> None:
    assert detect_c2pa(tiny_png()) is False
    assert detect_c2pa(b"\x89PNG....caBX....") is True
    assert detect_c2pa(b"....jumb....c2pa....") is True
    assert detect_c2pa(b"....jumb....") is False


# Live ---------------------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set")
def test_gemini_live_smoke(tmp_path: Path) -> None:
    """One small real picture (about 3 cents). Run with ``pytest -m live``."""
    provider = GeminiImageProvider()
    assert provider.health().status == "ok"
    result = provider.generate(ImageRequest(
        prompt="A calm mountain lake at sunrise, soft light, no text, no people",
        output_path=tmp_path / "live.png", aspect="16:9", size="1K",
    ))
    assert result.path.is_file() and result.width > result.height
    assert result.provenance.synthid is True
    assert result.cost_usd > 0
