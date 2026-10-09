"""LLM layer: config and prices, prompt templates, framework files, the mock, the call log,
and the request shape the real client would send (without calling the network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from cashcow_studio.llm import LLMError, MockLLMClient, build_llm_client
from cashcow_studio.llm.client import (
    AnthropicLLMClient,
    LLMRefused,
    provider_name,
    system_blocks,
)
from cashcow_studio.llm.config import (
    LLMConfig,
    ModelPrice,
    compute_cost,
    speaking_rate_wpm,
    transition_catalog,
)
from cashcow_studio.llm.frameworks import (
    FrameworkError,
    cache_path,
    check_framework_path,
    extract_text,
    framework_text,
    resolve_path,
    select_framework,
)
from cashcow_studio.llm.log import list_llm_calls, project_llm_cost
from cashcow_studio.llm.prompts import PromptError, available_prompts, load_prompt, render_text
from cashcow_studio.llm.text import (
    contains_keyword,
    keywords_of,
    ngram_overlap,
    similarity,
    split_sentences,
)
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.script import ScriptDraft, SpeechNormalizeOutput
from cashcow_studio.models.title import TitleLLMOutput

TEST_KEY = "sk-test-not-real-0000"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# config -----------------------------------------------------------------------------------


def test_llm_yaml_models_and_effort_follow_the_notes() -> None:
    config = LLMConfig.load()
    assert config.model_for("title") == "claude-opus-5-5"
    assert config.model_for("script") == "claude-opus-5-5"
    assert config.model_for("storyboard") == "claude-sonnet-5-5"
    assert config.model_for("transcript_summary") == "claude-sonnet-5-5"
    assert config.effort_for("script") == "high"
    assert config.effort_for("title") == "high"
    assert config.effort_for("storyboard") == "medium"
    assert config.max_tokens_for("script") == 64000
    assert config.max_tokens_for("title") == 16000
    assert config.streams("script") and not config.streams("title")
    assert config.fallback_enabled and config.fallback_beta == "server-side-fallback-2026-07-01"
    assert "claude-opus-5-5" in config.prices and "claude-sonnet-5-5" in config.prices


def test_settings_overrides_change_model_and_effort_per_task() -> None:
    config = LLMConfig.load({"models": {"title": "claude-sonnet-5-5"}, "effort": {"title": "max"}})
    assert config.model_for("title") == "claude-sonnet-5-5"
    assert config.effort_for("title") == "max"
    assert config.model_for("script") == "claude-opus-5-5"
    bad = LLMConfig.load({"effort": {"title": "turbo"}})
    assert bad.effort_for("title") == "medium"  # unknown level falls back


def test_cost_is_computed_from_the_price_table() -> None:
    price = ModelPrice(input=4.0, output=20.0, cache_read=0.2, cache_write_5m=5.0,
                       cache_write_1h=8.0)
    cost = compute_cost(
        price, input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=500_000,
        cache_write_1h_tokens=250_000,
    )
    assert cost == pytest.approx(4.0 + 2.0 + 0.1 + 2.0)
    assert compute_cost(ModelPrice(), input_tokens=10, output_tokens=10) == 0.0
    config = LLMConfig.load()
    assert config.price_for("claude-opus-5-5").output == 20.0
    assert config.price_for("unknown-model") == ModelPrice()


def test_speaking_rate_and_transitions_come_from_config() -> None:
    assert speaking_rate_wpm("English") == 150
    assert speaking_rate_wpm("Arabic") == 130
    assert speaking_rate_wpm("Other") == 150
    catalog = transition_catalog()
    assert len(catalog.types) >= 20
    assert catalog.default in catalog.types and catalog.last_scene in catalog.types
    assert catalog.get("wipeleft").label == "Wipe left"
    assert catalog.duration_for("fade", "shorts") < catalog.duration_for("fade", "long")
    assert catalog.duration_for("not-a-transition") == catalog.duration_for(catalog.default)


# prompts ------------------------------------------------------------------------------------


def test_every_prompt_file_has_system_and_user_sections() -> None:
    names = available_prompts()
    assert {"title", "script", "speech_normalize", "storyboard", "transcript_summary",
            "policy_check"} <= set(names)
    for name in names:
        prompt = load_prompt(name)
        assert prompt.system and prompt.user
        assert prompt.placeholders("system") == [], f"{name}: the cached block must be stable"
    assert "faceless" in load_prompt("title").default_framework
    assert "Hook" in load_prompt("script").default_framework


def test_render_replaces_placeholders_and_rejects_missing_values() -> None:
    assert render_text("Hello {{name}}, {{items}}", {"name": "Ann", "items": ["a", "b"]}) == (
        "Hello Ann, - a\n- b\n"
    )
    with pytest.raises(PromptError, match="needs a value for {{who}}"):
        render_text("Hi {{who}}", {})
    prompt = load_prompt("speech_normalize")
    text = prompt.render("user", {"language": "English", "sentences_json": "[]"})
    assert "Language: English" in text and "{{" not in text


# frameworks ---------------------------------------------------------------------------------


def _channel(frameworks: list[dict[str, Any]]) -> Channel:
    return Channel.model_validate(
        {"slug": "kind-ledger", "channel": {"name": "Kind Ledger"}, "frameworks": frameworks}
    )


def test_framework_text_reads_txt_and_md_and_caches(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    folder = shared / "channels" / "kind-ledger" / "frameworks"
    folder.mkdir(parents=True)
    (folder / "title.txt").write_text("My title rules\r\n\r\n\r\nLine two  \n", encoding="utf-8")
    channel = _channel([
        {"type": "title", "name": "Rules", "path": "channels/kind-ledger/frameworks/title.txt"}
    ])
    cache_dir = tmp_path / "app" / "cache" / "frameworks"
    result = framework_text(channel, ("title",), "long", shared, "DEFAULT", cache_dir=cache_dir)
    assert result.source == "file" and result.text == "My title rules\n\nLine two"
    # The extracted text is cached under the app folder, never next to the synced source.
    cache = cache_path(folder / "title.txt", cache_dir)
    assert cache.parent == cache_dir and cache.suffix == ".txt"
    assert cache.read_text(encoding="utf-8") == result.text
    assert [p.name for p in folder.iterdir()] == ["title.txt"]
    # The cache is used while it is newer than the source.
    cache.write_text("cached version", encoding="utf-8")
    assert extract_text(folder / "title.txt", cache_dir) == "cached version"
    # Without a cache folder the file is simply read every time.
    assert extract_text(folder / "title.txt") == "My title rules\n\nLine two"


def test_framework_paths_never_leave_the_shared_folder(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    (shared / "channels" / "kind-ledger" / "frameworks").mkdir(parents=True)
    (shared / "channels" / "kind-ledger" / "frameworks" / "ok.md").write_text("Rules", "utf-8")
    secret = tmp_path / "app" / ".env"
    secret.parent.mkdir(parents=True)
    secret.write_text("ANTHROPIC_API_KEY=sk-ant-not-for-the-model\n", encoding="utf-8")
    (shared / "notes.exe").write_bytes(b"MZ")

    for raw in (
        str(secret),                      # a full path
        "../app/.env",                    # climbing out of the shared folder
        "channels/kind-ledger/../../../app/.env",
        "C:/Users/someone/.env",
        "channels/kind-ledger/.env",      # a hidden file inside the folder
        "notes.exe",                      # not a text or PDF file
    ):
        assert check_framework_path(raw) is not None, raw
        with pytest.raises(FrameworkError):
            resolve_path(raw, shared)
        channel = _channel([{"type": "title", "name": "Sneaky", "path": raw}])
        result = framework_text(channel, ("title",), "long", shared, "DEFAULT")
        assert result.source == "default" and result.text == "DEFAULT", raw
        assert "sk-ant" not in result.text and "could not be used" in result.note, raw
    assert not list(secret.parent.glob("*.txt"))

    assert check_framework_path("channels/kind-ledger/frameworks/ok.md") is None
    assert check_framework_path("") is None
    assert check_framework_path("https://docs.google.com/document/d/abc") is None
    good = _channel([
        {"type": "title", "name": "Ok", "path": "channels/kind-ledger/frameworks/ok.md"}
    ])
    assert framework_text(good, ("title",), "long", shared, "D").text == "Rules"
    link = _channel([{"type": "title", "name": "Doc", "path": "https://docs.google.com/d/x"}])
    assert framework_text(link, ("title",), "long", shared, "D").is_default


def test_framework_text_reads_pdf(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DictionaryObject, NameObject, StreamObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    fonts = DictionaryObject({NameObject("/F1"): font_ref})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    stream = StreamObject()
    stream.set_data(b"BT /F1 18 Tf 20 150 Td (Framework from PDF) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "fw.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    cache_dir = tmp_path / "cache"
    text = extract_text(path, cache_dir)
    assert "Framework from PDF" in text
    assert cache_path(path, cache_dir).is_file()
    assert not (tmp_path / "fw.extracted.txt").exists()


def test_missing_framework_falls_back_to_default_with_a_note(tmp_path: Path) -> None:
    channel = _channel([
        {"type": "script_long", "name": "Lost", "path": "nowhere/x.pdf", "formats": "long"}
    ])
    result = framework_text(channel, ("script_long",), "long", tmp_path, "DEFAULT TEXT")
    assert result.source == "default" and result.text == "DEFAULT TEXT"
    assert "could not be used" in result.note and "Lost" in result.note
    # Format filter: a long-only framework is not used for Shorts.
    assert select_framework(channel, ("script_long",), "shorts") is None
    assert framework_text(_channel([]), ("title",), "long", tmp_path, "D").is_default
    with pytest.raises(FrameworkError):
        extract_text(tmp_path / "missing.txt")


# text helpers --------------------------------------------------------------------------------


def test_text_helpers() -> None:
    assert split_sentences("Mr. Lee waited. Then he left! Really? Yes.") == [
        "Mr. Lee waited.", "Then he left!", "Really?", "Yes."
    ]
    assert split_sentences('"Go," she said. He went.') == ['"Go," she said.', "He went."]
    # "How" and "One" are stopwords.
    assert keywords_of("How One Kind Stranger Changed a Life") == [
        "Kind", "Stranger", "Changed", "Life"
    ]
    assert contains_keyword("The strangers who stayed", "stranger")
    assert not contains_keyword("Nothing here", "stranger")
    assert similarity("Hello World", "hello world") == 1.0
    assert similarity("abc", "") == 0.0
    assert ngram_overlap("one two three", "one two three", 8) == 0.0  # shorter than n


# the mock -------------------------------------------------------------------------------------


def test_mock_is_selected_by_env_and_answers_every_task(app_env, monkeypatch) -> None:
    monkeypatch.setenv("CCS_LLM_PROVIDER", "mock")
    client = build_llm_client(app_data_dir=app_env.app_data_dir)
    assert isinstance(client, MockLLMClient) and client.provider == "mock"
    assert provider_name(None, LLMConfig.load()) == "mock"

    variables = {"source_title": "How One Kind Stranger Changed a Life",
                 "keywords_list": ["Kind", "Stranger", "Changed", "Life"]}
    output, usage = run(client.complete("title", ["fw", "sys"], "user", TitleLLMOutput,
                                        variables=variables, project_id="p1", stage="title"))
    assert len(output.variants) == 7
    assert all(len(v.title) < 70 for v in output.variants)
    assert all(v.keywords_kept for v in output.variants)
    assert len({v.title for v in output.variants}) == 7
    again, _ = run(client.complete("title", "sys", "user", TitleLLMOutput, variables=variables))
    assert again == output  # deterministic
    assert usage.model == "mock" and usage.cost_usd == 0.0 and usage.input_tokens > 0

    draft, _ = run(client.complete("script", "s", "u", ScriptDraft, variables={
        "title": "The Kind Stranger Nobody Talks About", "target_words": 300, "format": "long",
        "locked_paragraphs": [{"id": "p-02-01", "section": "Setup", "text": "KEEP ME."}],
    }))
    words = sum(len(p.text.split()) for s in draft.sections for p in s.paragraphs)
    assert 270 <= words <= 330
    assert draft.sections[0].paragraphs[0].text.startswith("The Kind Stranger Nobody Talks About")
    locked = [p for s in draft.sections for p in s.paragraphs if p.locked_id == "p-02-01"]
    assert locked and locked[0].text == "KEEP ME."

    rows = [{"id": "a", "text": "In 1998 Dr. Lee paid $1,200 for 45% more."}]
    speech, _ = run(client.complete("speech_normalize", "s", "u", SpeechNormalizeOutput,
                                    variables={"sentences": rows}))
    spoken = speech.sentences[0].speech_text
    assert "nineteen ninety-eight" in spoken and "Doctor" in spoken
    assert "one thousand two hundred dollars" in spoken and "forty-five percent" in spoken

    rows = list_llm_calls(app_env.app_data_dir, "p1")
    assert len(rows) == 1 and rows[0]["task"] == "title" and rows[0]["stage"] == "title"
    assert rows[0]["input_tokens"] > 0 and rows[0]["status"] == "ok"
    assert project_llm_cost(app_env.app_data_dir, "p1") == 0.0
    assert client.calls[0]["task"] == "title"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        build_llm_client(provider="gpt")


# the real client, without the network -----------------------------------------------------


class _Answer(BaseModel):
    ok: bool
    note: str = ""


class _Usage:
    input_tokens = 1200
    output_tokens = 300
    cache_read_input_tokens = 1000
    cache_creation_input_tokens = 0
    cache_creation = None


class _Message:
    def __init__(self, stop_reason: str = "end_turn", model: str = "claude-opus-5-5",
                 parsed: Any = None, category: str | None = None) -> None:
        self.stop_reason = stop_reason
        self.model = model
        self.parsed_output = parsed
        self.usage = _Usage()
        self._request_id = "req_test"
        self.content = []
        self.stop_details = type("Details", (), {"category": category, "explanation": None})()


def _refusal(category: str) -> _Message:
    return _Message(stop_reason="refusal", category=category)


def _fake_client(answers: list[_Message], sent: list[dict[str, Any]]) -> Any:
    class _Messages:
        async def parse(self, **params: Any) -> _Message:
            sent.append(params)
            return answers.pop(0)

        def stream(self, **params: Any) -> Any:
            sent.append(params)
            message = answers.pop(0)

            class _Stream:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *exc: Any) -> None:
                    return None

                async def get_final_message(self) -> _Message:
                    return message

            return _Stream()

    class _Beta:
        messages = _Messages()

    class _SDK:
        beta = _Beta()

    return _SDK()


def test_real_client_is_rebuilt_when_the_key_changes(app_env, monkeypatch) -> None:
    """A key replaced in Settings must be used at once, not after a restart."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-OLD-0123456789")
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    first = client._sdk()  # only builds the SDK object; nothing is sent
    assert first.api_key.endswith("OLD-0123456789")
    assert client._sdk() is first  # same key, same client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-NEW-0123456789")
    second = client._sdk()
    assert second is not first and second.api_key.endswith("NEW-0123456789")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(LLMError, match="No Anthropic API key"):
        client._sdk()


def test_real_client_request_shape_cost_and_log(app_env, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    sent: list[dict[str, Any]] = []
    client._client = _fake_client([_Message(parsed=_Answer(ok=True))], sent)
    parsed, usage = run(client.complete(
        "title", ["FRAMEWORK", "TASK"], "USER", _Answer, project_id="p9", stage="title"
    ))
    assert parsed.ok is True
    params = sent[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["max_tokens"] == 16000
    assert params["output_config"] == {"effort": "high"}
    assert params["output_format"] is _Answer
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["fallbacks"] == "default"
    assert "thinking" not in params and "temperature" not in params
    assert params["messages"] == [{"role": "user", "content": "USER"}]
    assert params["system"][0]["text"] == "FRAMEWORK"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in params["system"][1]
    # 1200 in * $4 + 300 out * $20 + 1000 cached * $0.20, per million
    assert usage.cost_usd == pytest.approx((1200 * 4 + 300 * 20 + 1000 * 0.2) / 1e6)
    assert usage.request_id == "req_test" and usage.fallback_used is False
    rows = list_llm_calls(app_env.app_data_dir, "p9")
    assert rows[0]["cost_usd"] == pytest.approx(usage.cost_usd)
    assert rows[0]["cache_read_input_tokens"] == 1000 and rows[0]["request_id"] == "req_test"


def test_real_client_streams_long_tasks_and_detects_fallback(app_env, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    sent: list[dict[str, Any]] = []
    client._client = _fake_client(
        [_Message(parsed=_Answer(ok=True), model="claude-opus-4-8")], sent
    )
    _, usage = run(client.complete("script", "sys", "user", _Answer))
    assert sent[0]["max_tokens"] == 64000  # streamed through beta.messages.stream
    assert usage.fallback_used is True and usage.model == "claude-opus-4-8"
    assert usage.requested_model == "claude-opus-5-5"


def test_real_client_retries_a_refusal_once_then_fails(app_env, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    client = AnthropicLLMClient(LLMConfig.load(), app_env.app_data_dir)
    sent: list[dict[str, Any]] = []
    client._client = _fake_client(
        [_refusal("general_harms"), _Message(parsed=_Answer(ok=True))], sent
    )
    parsed, usage = run(client.complete("title", "sys", "user", _Answer, project_id="p2"))
    assert parsed.ok and usage.attempts == 2
    assert sent[1]["messages"][0]["content"].endswith("user")
    assert sent[1]["messages"][0]["content"] != "user"  # reworded
    rows = list_llm_calls(app_env.app_data_dir, "p2")
    assert {r["status"] for r in rows} == {"refused", "ok"}

    client._client = _fake_client([_refusal("bio"), _refusal("bio")], sent)
    with pytest.raises(LLMRefused, match="bio"):
        run(client.complete("title", "sys", "user", _Answer))


def test_real_client_without_key_gives_a_plain_message(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = AnthropicLLMClient(LLMConfig.load())
    with pytest.raises(LLMError, match="Settings > API keys"):
        run(client.complete("title", "sys", "user", _Answer))


def test_system_blocks_put_cache_mark_on_the_first_block() -> None:
    blocks = system_blocks(["framework", "", "task"], "1h")
    assert [b["text"] for b in blocks] == ["framework", "task"]
    assert blocks[0]["cache_control"]["ttl"] == "1h" and "cache_control" not in blocks[1]
    assert system_blocks("only", "1h")[0]["cache_control"]["type"] == "ephemeral"
    assert system_blocks([], "1h") == []


def test_llm_calls_never_store_prompts(app_env, monkeypatch) -> None:
    monkeypatch.setenv("CCS_LLM_PROVIDER", "mock")
    client = build_llm_client(app_data_dir=app_env.app_data_dir)
    run(client.complete("title", "SECRET-SYSTEM", "SECRET-USER", TitleLLMOutput,
                        variables={"source_title": "A title"}, project_id="p3"))
    dump = json.dumps(list_llm_calls(app_env.app_data_dir, "p3"))
    assert "SECRET" not in dump
