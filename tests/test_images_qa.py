"""``LLMClient.analyze_image``: the mock's pass/fail hooks and call log, the real client's
request shape (image block before the text, Sonnet at effort low, structured output) against a
fake SDK, the refusal retry, picture shrinking, and the QA prompt and verdict helpers."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cashcow_studio.images.qa import check_image, join_reasons, qa_prompt, verdict_text
from cashcow_studio.llm import LLMError, MockLLMClient
from cashcow_studio.llm.client import (
    AnthropicLLMClient,
    LLMRefused,
    encode_image_for_vision,
    vision_content,
)
from cashcow_studio.llm.config import LLMConfig
from cashcow_studio.llm.log import list_llm_calls
from cashcow_studio.llm.prompts import load_prompt
from cashcow_studio.models.images import ImageQA

TEST_KEY = "sk-test-not-real-0000"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def png(path: Path, size: tuple[int, int] = (320, 180)) -> Path:
    Image.new("RGB", size, (30, 60, 90)).save(path, "PNG")
    return path


# Verdict maths ------------------------------------------------------------------------------------


def test_image_qa_verdicts_and_text() -> None:
    good = ImageQA(matches_prompt=True, has_text=False, has_real_person=False, has_logo=False,
                   score=7, reason="Fine.")
    assert good.accepted() and good.rejection_reasons() == []
    assert verdict_text(good) == "Passed (7/10)"
    bad = ImageQA(matches_prompt=False, has_text=True, has_real_person=True, has_logo=True,
                  score=3, artifacts=["six fingers"], reason="Bad.")
    reasons = bad.rejection_reasons()
    assert len(reasons) == 5 and reasons[-1].startswith("the quality score is 3")
    assert not bad.accepted() and bad.accepted(min_score=0) is False
    assert verdict_text(bad).startswith("Rejected: it does not show what the scene asked for, ")
    assert verdict_text(None) == "Not checked"
    low = ImageQA(matches_prompt=True, has_text=False, has_real_person=False, has_logo=False,
                  score=4)
    assert not low.accepted() and low.accepted(min_score=4)
    assert join_reasons([]) == "" and join_reasons(["a"]) == "a"
    assert join_reasons(["a", "b", "c"]) == "a, b and c"
    with pytest.raises(ValueError):
        ImageQA(matches_prompt=True, has_text=False, has_real_person=False, has_logo=False,
                score=11)


def test_qa_prompt_comes_from_the_prompt_file() -> None:
    template = load_prompt("image_qa")
    assert template.placeholders("system") == []
    text = qa_prompt("A diner at dawn", "text, logos", "The diner opened at five.")
    assert "A diner at dawn" in text and "text, logos" in text and "opened at five" in text
    assert "{{" not in text and "matches_prompt" in text and "first try" in text
    assert "(no description)" in qa_prompt("", "", "")
    retry = qa_prompt("x", "", "", "The previous picture was rejected because it had text.")
    assert "rejected because it had text" in retry and "first try" not in retry


# The mock -----------------------------------------------------------------------------------------


def test_mock_analyze_image_pass_fail_and_log(app_env, tmp_path: Path) -> None:
    client = MockLLMClient(app_data_dir=app_env.app_data_dir)
    picture = png(tmp_path / "scene.png")
    verdict, cost = run(check_image(client, picture, "A diner at dawn", project_id="p1"))
    assert verdict.accepted() and verdict.score == 8 and cost == 0.0
    assert client.last_usage is not None and client.last_usage.task == "image_qa"
    assert client.calls[-1]["task"] == "image_qa" and client.calls[-1]["size"] == (320, 180)

    failed = run(client.analyze_image("image_qa", picture, "A diner FAIL_QA", ImageQA))
    assert not failed.matches_prompt and failed.score == 2 and not failed.has_text
    text = run(client.analyze_image("image_qa", picture, "A diner FAIL_QA_TEXT", ImageQA))
    assert text.has_text and not text.has_logo
    once = run(client.analyze_image("image_qa", picture, "FAIL_QA_ONCE first try", ImageQA))
    assert not once.accepted()
    retried = run(client.analyze_image(
        "image_qa", picture, "FAIL_QA_ONCE The previous picture was rejected because x", ImageQA
    ))
    assert retried.accepted()

    class Other(ImageQA):
        pass

    other = run(client.analyze_image("thumbnail_template", picture, "anything", Other))
    assert isinstance(other, Other)  # unknown tasks get an empty, schema-valid answer

    with pytest.raises(LLMError, match="could not read"):
        run(client.analyze_image("image_qa", tmp_path / "missing.png", "x", ImageQA))
    rows = list_llm_calls(app_env.app_data_dir, "p1")
    assert len(rows) == 1 and rows[0]["task"] == "image_qa" and rows[0]["stage"] == "images"
    assert rows[0]["cost_usd"] == 0.0 and rows[0]["input_tokens"] > 1500


# The real client, without the network -------------------------------------------------------------


class _Usage:
    input_tokens = 1600
    output_tokens = 80
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    cache_creation = None


class _Message:
    def __init__(self, stop_reason: str = "end_turn", parsed: Any = None,
                 category: str | None = None) -> None:
        self.stop_reason = stop_reason
        self.model = "claude-sonnet-5-5"
        self.parsed_output = parsed
        self.usage = _Usage()
        self._request_id = "req_img"
        self.content = []
        self.stop_details = type("Details", (), {"category": category, "explanation": None})()


def _fake_client(answers: list[_Message], sent: list[dict[str, Any]]) -> Any:
    class _Messages:
        async def parse(self, **params: Any) -> _Message:
            sent.append(params)
            return answers.pop(0)

    class _Beta:
        messages = _Messages()

    class _SDK:
        beta = _Beta()

    return _SDK()


def good_verdict() -> ImageQA:
    return ImageQA(matches_prompt=True, has_text=False, has_real_person=False, has_logo=False,
                   score=9, reason="Clean.")


def test_real_client_sends_the_image_before_the_text(app_env, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    sent: list[dict[str, Any]] = []
    client._client = _fake_client([_Message(parsed=good_verdict())], sent)
    picture = png(tmp_path / "scene_01.png")
    verdict = run(client.analyze_image("image_qa", picture, "Judge this", ImageQA,
                                       project_id="p7", stage="images"))
    assert verdict.score == 9
    params = sent[0]
    assert params["model"] == "claude-sonnet-5-5"
    assert params["output_config"] == {"effort": "low"}
    assert params["output_format"] is ImageQA and params["max_tokens"] == 16000
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in params and "temperature" not in params and "system" not in params
    content = params["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[1] == {"type": "text", "text": "Judge this"}
    source = content[0]["source"]
    assert source["type"] == "base64" and source["media_type"] == "image/png"
    assert base64.b64decode(source["data"]) == picture.read_bytes()
    usage = client.last_usage
    assert usage is not None and usage.cost_usd == pytest.approx((1600 * 2 + 80 * 10) / 1e6)
    rows = list_llm_calls(app_env.app_data_dir, "p7")
    assert rows[0]["task"] == "image_qa" and rows[0]["stage"] == "images"
    assert rows[0]["request_id"] == "req_img"


def test_real_client_retries_a_refusal_once_for_pictures(app_env, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    sent: list[dict[str, Any]] = []
    client._client = _fake_client(
        [_Message(stop_reason="refusal", category="general_harms"),
         _Message(parsed=good_verdict())], sent,
    )
    picture = png(tmp_path / "p.png")
    verdict = run(client.analyze_image("image_qa", picture, "Judge", ImageQA))
    assert verdict.accepted() and len(sent) == 2
    second_text = sent[1]["messages"][0]["content"][1]["text"]
    assert second_text.endswith("Judge") and second_text != "Judge"
    assert sent[1]["messages"][0]["content"][0]["type"] == "image"
    client._client = _fake_client(
        [_Message(stop_reason="refusal", category="x"), _Message(stop_reason="refusal",
                                                                 category="x")], sent,
    )
    with pytest.raises(LLMRefused):
        run(client.analyze_image("image_qa", picture, "Judge", ImageQA))
    client._client = _fake_client([_Message(parsed=None)], sent)
    with pytest.raises(LLMError, match="expected shape"):
        run(client.analyze_image("image_qa", picture, "Judge", ImageQA))


def test_big_pictures_are_shrunk_before_the_check(tmp_path: Path) -> None:
    small = png(tmp_path / "small.png")
    data, media = encode_image_for_vision(small)
    assert media == "image/png" and base64.b64decode(data) == small.read_bytes()
    big = png(tmp_path / "big.png", size=(4096, 2304))
    data, media = encode_image_for_vision(big)
    assert media == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(data))) as image:
        assert image.size == (1568, 882) and image.format == "JPEG"
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (100, 50), (1, 2, 3)).save(jpg, "JPEG")
    assert encode_image_for_vision(jpg)[1] == "image/jpeg"
    with pytest.raises(LLMError, match="could not be read"):
        encode_image_for_vision(tmp_path / "missing.png")
    blocks = vision_content("abc", "image/png", "hi")
    assert [b["type"] for b in blocks] == ["image", "text"]
