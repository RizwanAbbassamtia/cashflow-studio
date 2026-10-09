"""Script stage on the mock: the originality maths, the files, locked paragraphs on a redo,
the gates (blocked when copying), the channel's script history and approval edits."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from cashcow_studio.config import Settings
from cashcow_studio.llm import LLMUsage, MockLLMClient
from cashcow_studio.llm.client import LLMError
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.models.script import (
    PolicyCheckOutput,
    ScriptDoc,
    ScriptDraft,
    ScriptDraftParagraph,
    ScriptDraftSection,
    SpeechNormalizeOutput,
    TranscriptSummary,
)
from cashcow_studio.pipeline.stages.base import GateBlocked, StageContext, StageError
from cashcow_studio.pipeline.stages.script import (
    ScriptStage,
    fingerprint,
    load_review_payload,
    ngram_overlap,
    ngrams,
    parse_script_markdown,
    recent_scripts,
    script_to_markdown,
    split_sentences,
    target_word_count,
    tokenize,
)

TITLE = "The Kind Stranger Nobody Talks About"
TRANSCRIPT = ("So this stranger walked into the diner one cold morning and nobody knew what "
              "to expect from him at all. ") * 12


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_channel(minutes: int = 1, seconds: int = 45) -> Channel:
    return Channel.model_validate({
        "slug": "kind-ledger",
        "channel": {"name": "Kind Ledger", "niche": "Kindness stories", "audience": "Adults 35+",
                    "long_form_minutes": minutes, "shorts_seconds": seconds},
    })


def make_project(folder: Path, *, project_id: str = "p1", title: str = TITLE,
                 fmt: str = "long", with_transcript: bool = True) -> Project:
    now = datetime.now(UTC)
    research = folder / "01_research"
    research.mkdir(parents=True, exist_ok=True)
    if with_transcript:
        (research / "transcript.json").write_text(json.dumps({
            "video_id": "abc", "segments": [{"start": 0, "end": 5, "text": TRANSCRIPT}],
        }), encoding="utf-8")
    return Project(id=project_id, channel_slug="kind-ledger", topic_slug="kind-stranger",
                   title=title, format=fmt, created_at=now, updated_at=now, folder=str(folder),
                   source=ProjectSource(kind="ai_pick", video_id="abc"))


def make_ctx(app_env, project: Project, *, channel: Channel | None = None, llm: Any = None,
             notes: list[str] | None = None, edits: dict | None = None) -> StageContext:
    settings = Settings()
    return StageContext(
        project=project, channel=channel or make_channel(), settings=settings,
        folder=Path(project.folder),
        providers={"llm": llm or MockLLMClient(app_data_dir=settings.app_data_dir)},
        notes=notes or [], edits=edits or {},
    )


def read(folder: Path, name: str) -> dict:
    return json.loads((folder / "03_script" / name).read_text(encoding="utf-8"))


def paragraphs_of(script: dict) -> list[dict]:
    return [p for s in script["sections"] for p in s["paragraphs"]]


# Pure maths ---------------------------------------------------------------------------------


def test_ngram_overlap_hand_computed() -> None:
    candidate = "a b c d e f g h i j"          # 10 tokens -> three 8-grams
    reference = "x a b c d e f g h y"          # contains exactly the first one
    assert ngrams(tokenize(candidate), 8) == [
        tuple("abcdefgh"), tuple("bcdefghi"), tuple("cdefghij")
    ]
    assert ngram_overlap(candidate, reference, 8) == pytest.approx(1 / 3, abs=1e-4)
    assert ngram_overlap(candidate, candidate, 8) == 1.0
    assert ngram_overlap(candidate, "nothing shared here at all with the other text", 8) == 0.0
    assert ngram_overlap("too short", reference, 8) == 0.0
    assert ngram_overlap(candidate, "", 8) == 0.0
    # Punctuation and case do not matter; repeats count per position.
    assert ngram_overlap("A, b: C d e F g H!", "a b c d e f g h", 8) == 1.0
    twice = "one two three four five six seven eight. one two three four five six seven eight."
    once = "one two three four five six seven eight"
    assert ngram_overlap(twice, once, 8) == pytest.approx(2 / 9, abs=1e-4)
    assert fingerprint("Hello, World!") == fingerprint("hello world")


def test_sentences_and_targets() -> None:
    assert split_sentences("First one. Second one? Third!") == [
        "First one.", "Second one?", "Third!"
    ]
    assert target_word_count("long", make_channel(minutes=10), 150) == (1500, "10 minutes")
    assert target_word_count("shorts", make_channel(seconds=45), 150) == (112, "45 seconds")
    assert target_word_count("long", make_channel(minutes=10), 140)[0] == 1400


def test_markdown_round_trip() -> None:
    doc = ScriptDoc.model_validate({
        "title": "T", "language": "English", "format": "long", "target_words": 10,
        "word_count": 9, "model": "mock", "generated_at": datetime.now(UTC),
        "sections": [{"id": "sec-01", "name": "Hook", "purpose": "Open strong.", "paragraphs": [
            {"id": "p-01-01", "text": "One. Two.", "sentences": [], "locked": True},
            {"id": "p-01-02", "text": "Three.", "sentences": []},
        ]}, {"id": "sec-02", "name": "End", "purpose": "", "paragraphs": [
            {"id": "p-02-01", "text": "Four five.", "sentences": []},
        ]}],
    })
    markdown = script_to_markdown(doc)
    assert markdown.startswith(
        "# T\n\n## Hook\n_Purpose: Open strong._\n\nOne. Two.\n\nThree.\n\n## End\n"
    )
    sections = parse_script_markdown(markdown, doc)
    assert [s.name for s in sections] == ["Hook", "End"]
    assert sections[0].purpose == "Open strong." and sections[1].purpose == ""
    assert [p.text for p in sections[0].paragraphs] == ["One. Two.", "Three."]
    assert sections[0].paragraphs[0].locked and not sections[0].paragraphs[1].locked
    assert [s.id for s in sections[0].paragraphs[0].sentences] == ["s-01-01-01", "s-01-01-02"]
    # An edit that moves the locked paragraph keeps its lock (matched by text).
    moved = markdown.replace("One. Two.\n\nThree.", "Three.\n\nOne. Two.")
    sections = parse_script_markdown(moved, doc)
    assert [p.locked for p in sections[0].paragraphs] == [False, True]
    with pytest.raises(StageError, match="no paragraphs"):
        parse_script_markdown("# Empty\n\n## Hook\n", doc)


# The stage ----------------------------------------------------------------------------------


def test_run_writes_the_four_files_and_passes_the_gates(app_env) -> None:
    folder = app_env.projects_dir / "kind-stranger"
    project = make_project(folder)
    ctx = make_ctx(app_env, project)
    result = run(ScriptStage().run(ctx))

    names = [p.name for p in result.outputs]
    assert names == ["script.json", "script.md", "speech.json", "originality.json"]
    assert (folder / "03_script" / "transcript_summary.json").is_file()
    script = read(folder, "script.json")
    assert script["title"] == project.title and script["target_words"] == 150
    assert script["format"] == "long" and script["language"] == "English"
    assert 128 <= script["word_count"] <= 172
    sections = script["sections"]
    assert len(sections) >= 2 and sections[0]["id"] == "sec-01"
    first = sections[0]["paragraphs"][0]
    assert first["id"] == "p-01-01" and first["sentences"][0]["id"] == "s-01-01-01"
    assert first["text"].startswith(project.title)  # the hook repeats the title
    assert first["locked"] is False
    sentence_ids = [s["id"] for p in paragraphs_of(script) for s in p["sentences"]]
    assert len(sentence_ids) == len(set(sentence_ids))

    markdown = (folder / "03_script" / "script.md").read_text(encoding="utf-8")
    assert markdown.startswith(f"# {project.title}\n") and "\n## Hook\n" in markdown

    speech = read(folder, "speech.json")
    assert [s["id"] for s in speech["sentences"]] == sentence_ids
    assert all(s["speech_text"] for s in speech["sentences"])

    originality = read(folder, "originality.json")
    assert originality["passed"] is True
    assert originality["ngram_overlap_source"] <= 0.02 and originality["source_compared"]
    assert originality["ngram_overlap_history_max"] == 0.0
    assert originality["history_compared"] == 0
    assert originality["policy"] == {
        "advisory_persona": False, "sensitive_topic": False, "reasons": []
    }
    assert originality["title_claim_early"] is True and originality["semantic_note"]
    assert {g["id"] for g in originality["gate_results"]} == {
        "script.ngram_source", "script.ngram_history", "script.advisory_persona",
        "script.word_count", "script.title_claim_early", "script.sensitive_topic",
    }
    assert all(g["passed"] for g in result.gate_results)

    payload = result.needs_review_payload
    assert payload["stage"] == "script" and payload["script_md"] == markdown
    assert payload["originality"]["passed"] and payload["transcript_summary"]["beats"]
    assert json.dumps(payload)
    assert load_review_payload(folder)["script"]["word_count"] == script["word_count"]
    rows = recent_scripts(ctx.settings.app_data_dir, "kind-ledger")
    assert len(rows) == 1 and rows[0]["project_id"] == "p1"
    assert rows[0]["path"].endswith("script.md")
    tasks = [c["task"] for c in ctx.providers["llm"].calls]
    assert tasks == ["transcript_summary", "script", "speech_normalize", "policy_check"]
    assert "words" in result.summary


def test_shorts_target_and_no_transcript(app_env) -> None:
    folder = app_env.projects_dir / "short"
    project = make_project(folder, fmt="shorts", with_transcript=False)
    ctx = make_ctx(app_env, project)
    run(ScriptStage().run(ctx))
    script = read(folder, "script.json")
    assert script["target_words"] == 112 and script["format"] == "shorts"
    assert 95 <= script["word_count"] <= 129
    originality = read(folder, "originality.json")
    assert originality["source_compared"] is False and originality["passed"]
    assert not (folder / "03_script" / "transcript_summary.json").exists()
    assert ctx.providers["llm"].calls[0]["task"] == "script"  # no summary call


def test_redo_keeps_locked_paragraphs_and_reuses_the_summary(app_env) -> None:
    folder = app_env.projects_dir / "redo"
    project = make_project(folder)
    ctx = make_ctx(app_env, project)
    run(ScriptStage().run(ctx))
    script = read(folder, "script.json")
    locked_id = script["sections"][1]["paragraphs"][0]["id"]
    locked_text = script["sections"][1]["paragraphs"][0]["text"]

    ctx2 = make_ctx(app_env, project, notes=["Warmer, please."],
                    edits={"locked_paragraph_ids": [locked_id]})
    run(ScriptStage().run(ctx2))
    kept = [p for p in paragraphs_of(read(folder, "script.json")) if p["locked"]]
    assert len(kept) == 1 and kept[0]["text"] == locked_text
    assert kept[0]["id"] == locked_id  # same section, same position
    calls = ctx2.providers["llm"].calls
    assert [c["task"] for c in calls] == ["script", "speech_normalize", "policy_check"]
    assert calls[0]["variables"]["notes"] == ["Warmer, please."]
    assert calls[0]["variables"]["locked_paragraphs"][0]["id"] == locked_id
    assert read(folder, "originality.json")["history_compared"] == 0  # own draft is not history


class CopyingLLM:
    """Writes the competitor transcript back as the script: the originality gate must block."""

    provider = "fake"

    async def complete(self, task: str, system: Any, user: str, schema: type[BaseModel],
                       *, variables: dict | None = None, **kw: Any):
        usage = LLMUsage(task=task, model="fake", requested_model="fake")
        if task == "transcript_summary":
            return TranscriptSummary(beats=[]), usage
        if task == "script":
            text = TRANSCRIPT.strip()
            return ScriptDraft(sections=[ScriptDraftSection(
                name="Hook", paragraphs=[ScriptDraftParagraph(text=text)]
            )]), usage
        if task == "speech_normalize":
            return SpeechNormalizeOutput(sentences=[]), usage
        return PolicyCheckOutput(advisory_persona=True, sensitive_topic=False,
                                 title_claim_early=False,
                                 reasons=["The narrator says 'you should buy gold'."]), usage


def test_copying_the_transcript_is_blocked_but_files_are_written(app_env) -> None:
    folder = app_env.projects_dir / "copy"
    project = make_project(folder)
    ctx = make_ctx(app_env, project, llm=CopyingLLM(), channel=make_channel(minutes=2))
    with pytest.raises(GateBlocked) as info:
        run(ScriptStage().run(ctx))
    reasons = info.value.reasons
    assert any("copy the competitor transcript" in r for r in reasons)
    assert any("you should buy gold" in r for r in reasons)
    assert any("the target is 300" in r for r in reasons)
    originality = read(folder, "originality.json")
    assert originality["passed"] is False and originality["ngram_overlap_source"] > 0.9
    assert originality["policy"]["advisory_persona"] is True
    assert originality["blocking_reasons"] == reasons
    assert (folder / "03_script" / "script.md").is_file()
    speech = read(folder, "speech.json")
    assert speech["sentences"][0]["speech_text"] == speech["sentences"][0]["text"]  # fallback


def test_same_script_on_the_channel_again_is_blocked_by_history(app_env) -> None:
    first = make_project(app_env.projects_dir / "one", project_id="p1")
    run(ScriptStage().run(make_ctx(app_env, first)))
    second = make_project(app_env.projects_dir / "two", project_id="p2")
    ctx = make_ctx(app_env, second)
    with pytest.raises(GateBlocked) as info:
        run(ScriptStage().run(ctx))
    assert any("one of the last 1 scripts" in r for r in info.value.reasons)
    originality = read(Path(second.folder), "originality.json")
    assert originality["ngram_overlap_history_max"] == 1.0
    assert originality["history_compared"] == 1
    # A different title gives a different script that passes.
    third = make_project(app_env.projects_dir / "three", project_id="p3",
                         title="Why the Diner Owner Never Locked the Door")
    result = run(ScriptStage().run(make_ctx(app_env, third)))
    originality = read(Path(third.folder), "originality.json")
    assert originality["passed"] and originality["history_compared"] == 2
    assert originality["ngram_overlap_history_max"] <= 0.05
    assert all(g["passed"] for g in result.gate_results if g["severity"] == "block")


def test_apply_edits_replaces_text_and_sets_locks(app_env) -> None:
    folder = app_env.projects_dir / "edits"
    project = make_project(folder)
    ctx = make_ctx(app_env, project)
    run(ScriptStage().run(ctx))
    script = read(folder, "script.json")
    speech_before = {
        s["text"]: s["speech_text"] for s in read(folder, "speech.json")["sentences"]
    }
    markdown = (folder / "03_script" / "script.md").read_text(encoding="utf-8")
    edited = markdown.replace("## Hook", "## Opening").replace(
        "That is the promise, and this is the story behind it.",
        "That is the promise. In 2019 it cost $40 to find out.",
    )
    ctx.edits = {"script_md": edited, "locked_paragraph_ids": ["p-01-01", "p-02-01"]}
    result = run(ScriptStage().apply_edits(ctx))
    script2 = read(folder, "script.json")
    assert script2["sections"][0]["name"] == "Opening"
    assert script2["sections"][0]["purpose"] == script["sections"][0]["purpose"]
    assert [p["id"] for p in paragraphs_of(script2) if p["locked"]] == ["p-01-01", "p-02-01"]
    speech2 = read(folder, "speech.json")
    changed = next(s for s in speech2["sentences"] if "2019" in s["text"])
    assert "twenty nineteen" in changed["speech_text"] and "dollars" in changed["speech_text"]
    unchanged = [s for s in speech2["sentences"] if s["text"] in speech_before]
    assert unchanged
    assert all(s["speech_text"] == speech_before[s["text"]] for s in unchanged)
    calls = [c for c in ctx.providers["llm"].calls if c["task"] == "speech_normalize"]
    assert len(calls[-1]["variables"]["sentences"]) < len(speech2["sentences"])  # new ones only
    originality = read(folder, "originality.json")
    assert originality["word_count"] == script2["word_count"]
    assert result.needs_review_payload["script_md"].startswith(f"# {project.title}")
    assert "edited by the reviewer" in result.summary

    ctx.edits = {"locked_paragraph_ids": []}
    run(ScriptStage().apply_edits(ctx))
    assert not any(p["locked"] for p in paragraphs_of(read(folder, "script.json")))
    ctx.edits = {}
    assert run(ScriptStage().apply_edits(ctx)) is None
    ctx.edits = {"script_md": 42}
    with pytest.raises(StageError, match="not valid"):
        run(ScriptStage().apply_edits(ctx))


def test_missing_title_is_a_plain_error(app_env) -> None:
    folder = app_env.projects_dir / "no-title"
    project = make_project(folder, title="")
    with pytest.raises(StageError, match="no approved title"):
        run(ScriptStage().run(make_ctx(app_env, project)))


class FailingLLM:
    provider = "fake"

    async def complete(self, *args: Any, **kwargs: Any):
        raise LLMError("Could not reach Claude. Check the internet connection and try again.")


def test_apply_edits_with_a_failing_model_leaves_the_files_untouched(app_env) -> None:
    folder = app_env.projects_dir / "offline"
    project = make_project(folder)
    ctx = make_ctx(app_env, project)
    run(ScriptStage().run(ctx))
    names = ("script.json", "script.md", "speech.json", "originality.json")
    before = {name: (folder / "03_script" / name).read_bytes() for name in names}
    markdown = (folder / "03_script" / "script.md").read_text(encoding="utf-8")
    ctx.providers = {"llm": FailingLLM()}
    ctx.edits = {"script_md": markdown.replace("## Hook", "## Opening") + "\nA brand new line.\n"}
    with pytest.raises(LLMError, match="Could not reach Claude"):
        run(ScriptStage().apply_edits(ctx))
    after = {name: (folder / "03_script" / name).read_bytes() for name in names}
    assert after == before  # nothing half-written: the model call comes before any write
