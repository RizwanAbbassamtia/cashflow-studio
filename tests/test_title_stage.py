"""Title stage on the mock: seven options, the gates, title.json, title_history, approvals."""

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
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.models.title import TitleLLMOutput, TitleSource, TitleVariantLLM
from cashcow_studio.pipeline.stages.base import GateBlocked, StageContext, StageError
from cashcow_studio.pipeline.stages.title import (
    TitleStage,
    choose_recommended,
    load_review_payload,
    load_source,
    measure_variants,
    recent_titles,
    upsert_title_history,
)

SOURCE_TITLE = "How One Kind Stranger Changed a Life"
KEYWORDS = ["Kind", "Stranger", "Changed", "Life"]  # "How" and "One" are stopwords
VARIANT_FIELDS = {
    "index", "title", "formula", "emotional_trigger", "curiosity_trigger", "hidden_gap",
    "viral_score", "why_it_outperforms", "keywords_kept", "similarity_to_source",
    "similarity_to_history", "length", "flags",
}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_channel(**channel: Any) -> Channel:
    identity = {"name": "Kind Ledger", "niche": "Kindness stories", "audience": "Adults 35+",
                "long_form_minutes": 1, "shorts_seconds": 45}
    identity.update(channel)
    return Channel.model_validate({"slug": "kind-ledger", "channel": identity})


def make_project(folder: Path, *, project_id: str = "p1", kind: str = "ai_pick",
                 topic: str | None = None, fmt: str = "long") -> Project:
    now = datetime.now(UTC)
    source = ProjectSource(kind=kind, video_id="abc" if kind != "own_topic" else None,
                           topic_text=topic)
    return Project(id=project_id, channel_slug="kind-ledger", topic_slug="kind-stranger",
                   title=topic or "", format=fmt, created_at=now, updated_at=now,
                   folder=str(folder), source=source)


def write_pick(folder: Path, title: str = SOURCE_TITLE) -> None:
    research = folder / "01_research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "pick.json").write_text(json.dumps({
        "kind": "ai_pick", "video_id": "abc", "url": "https://www.youtube.com/watch?v=abc",
        "title": title, "channel_name": "Human Ember",
        "candidate": {"views": 1_200_000, "outlier_score": 12.3},
    }), encoding="utf-8")


def make_ctx(app_env, *, project: Project, channel: Channel | None = None,
             llm: Any = None, notes: list[str] | None = None) -> StageContext:
    settings = Settings()
    folder = Path(project.folder)
    folder.mkdir(parents=True, exist_ok=True)
    return StageContext(
        project=project, channel=channel or make_channel(), settings=settings, folder=folder,
        providers={"llm": llm or MockLLMClient(app_data_dir=settings.app_data_dir)},
        notes=notes or [],
    )


def read_doc(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "02_title" / "title.json").read_text(encoding="utf-8"))


class CopyingLLM:
    """A model that returns the source title seven times: every option fails the gates."""

    provider = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, task: str, system: Any, user: str, schema: type[BaseModel],
                       **kw: Any):
        self.calls += 1
        variants = [TitleVariantLLM(title=SOURCE_TITLE, formula="copy", viral_score=5)
                    for _ in range(7)]
        return TitleLLMOutput(variants=variants, recommended_index=0), LLMUsage(
            task=task, model="fake", requested_model="fake"
        )


def test_run_writes_seven_gated_options(app_env) -> None:
    folder = app_env.projects_dir / "kind-stranger"
    write_pick(folder)
    project = make_project(folder)
    ctx = make_ctx(app_env, project=project)
    result = run(TitleStage().run(ctx))

    path = folder / "02_title" / "title.json"
    assert result.outputs == [path] and path.is_file()
    doc = read_doc(folder)
    assert len(doc["variants"]) == 7
    assert doc["source_title"] == SOURCE_TITLE and doc["source"]["views"] == 1_200_000
    assert doc["recommended_index"] is not None
    for variant in doc["variants"]:
        assert set(variant) >= VARIANT_FIELDS
        assert variant["length"] < 70 and variant["similarity_to_source"] < 0.8
        assert variant["keywords_kept"] and variant["flags"] == []
    recommended = doc["variants"][doc["recommended_index"]]
    assert project.title == recommended["title"]
    data_dir = ctx.settings.app_data_dir
    assert recent_titles(data_dir, "kind-ledger") == [recommended["title"]]
    assert recent_titles(data_dir, "kind-ledger", exclude_project_id="p1") == []
    assert all(g["passed"] for g in result.gate_results), result.gate_results
    assert {g["id"] for g in result.gate_results} == {
        "title.count", "title.length", "title.similarity_source", "title.similarity_history",
        "title.keywords", "title.recommendable",
    }
    payload = result.needs_review_payload
    assert payload["stage"] == "title" and payload["title"]["variants"][0]["title"]
    assert payload["rules"]["title.length"]["parameters"]["max_chars"] == 70
    assert "Recommended:" in result.summary
    assert json.dumps(payload)  # serialisable for the review endpoint
    rebuilt = load_review_payload(folder, data_dir, "kind-ledger", "p1")
    assert rebuilt["title"]["model"] == "mock"
    # The prompt variables carried the proven keywords; the framework was the built-in default.
    llm = ctx.providers["llm"]
    assert llm.calls[0]["variables"]["keywords_list"] == KEYWORDS
    assert doc["framework_source"] == "default"


def test_own_topic_without_research_files(app_env) -> None:
    folder = app_env.projects_dir / "own-topic"
    project = make_project(folder, kind="own_topic", topic="Why cats purr when they are hurt")
    ctx = make_ctx(app_env, project=project)
    source = load_source(folder, project)
    assert source.kind == "own_topic" and source.title == "Why cats purr when they are hurt"
    result = run(TitleStage().run(ctx))
    doc = read_doc(folder)
    assert doc["source"]["kind"] == "own_topic"
    assert all(v["flags"] == [] for v in doc["variants"])
    assert all(g["passed"] for g in result.gate_results)


def test_measure_variants_flags_each_gate() -> None:
    source = TitleSource(kind="ai_pick", title=SOURCE_TITLE)
    history = ["The Kind Stranger Story Nobody Talks About"]
    output = TitleLLMOutput(variants=[
        TitleVariantLLM(title="How One Kind Stranger Changed a Life!", formula="copy",
                        viral_score=9),
        TitleVariantLLM(title="The Kind Stranger Story Nobody Talks About", formula="repeat",
                        viral_score=8),
        TitleVariantLLM(title="A Night Nobody Expected To Remember Forever", formula="no kw",
                        viral_score=7),
        TitleVariantLLM(title="Kind Stranger " * 6, formula="long", viral_score=12),
        TitleVariantLLM(title="What a Stranger Taught Me About Trust", formula="clean",
                        viral_score=0),
    ], recommended_index=0)
    variants = measure_variants(output, source, history, KEYWORDS)
    assert [v.index for v in variants] == [0, 1, 2, 3, 4]
    assert variants[0].similarity_to_source > 0.9 and "competitor" in variants[0].flags[0]
    assert variants[1].similarity_to_history == 1.0 and "recent title" in variants[1].flags[0]
    assert variants[2].flags == ["Keeps no keyword from the source title."]
    assert variants[3].length >= 70 and variants[3].flags[0].startswith("Longer than 69")
    assert variants[3].viral_score == 10 and variants[4].viral_score == 1  # clamped
    assert variants[4].flags == [] and variants[4].keywords_kept == ["Stranger"]
    assert choose_recommended(variants, suggested=0) == 4  # model's pick failed; best clean wins
    assert choose_recommended(variants, suggested=4) == 4
    assert choose_recommended([variants[0]], suggested=0) is None


def test_history_similarity_uses_other_projects_titles(app_env) -> None:
    settings = Settings()
    upsert_title_history(settings.app_data_dir, "kind-ledger",
                         "The Kind Stranger Story Nobody Talks About", "older")
    folder = app_env.projects_dir / "kind-stranger-2"
    write_pick(folder)
    project = make_project(folder, project_id="p2")
    ctx = make_ctx(app_env, project=project)
    run(TitleStage().run(ctx))
    doc = read_doc(folder)
    assert doc["history_compared"] == 1
    flagged = [v for v in doc["variants"] if v["flags"]]
    assert flagged and "recent title" in flagged[0]["flags"][0]
    assert doc["recommended_index"] not in [v["index"] for v in flagged]
    gate = next(g for g in doc["gate_results"] if g["id"] == "title.similarity_history")
    assert not gate["passed"] and gate["severity"] == "warn"
    assert len(recent_titles(settings.app_data_dir, "kind-ledger")) == 2


def test_all_options_failing_blocks_after_one_retry(app_env) -> None:
    folder = app_env.projects_dir / "blocked"
    write_pick(folder)
    project = make_project(folder, project_id="p3")
    llm = CopyingLLM()
    ctx = make_ctx(app_env, project=project, llm=llm)
    with pytest.raises(GateBlocked) as info:
        run(TitleStage().run(ctx))
    assert llm.calls == 2
    assert "At least one option passes every check" in info.value.reasons[0]
    doc = read_doc(folder)
    assert doc["recommended_index"] is None
    assert all(v["flags"] for v in doc["variants"])
    assert project.title == ""
    assert recent_titles(ctx.settings.app_data_dir, "kind-ledger") == []


def test_apply_edits_sets_the_project_title(app_env) -> None:
    folder = app_env.projects_dir / "edits"
    write_pick(folder)
    project = make_project(folder, project_id="p4")
    ctx = make_ctx(app_env, project=project)
    run(TitleStage().run(ctx))
    stage = TitleStage()
    data_dir = ctx.settings.app_data_dir

    ctx.edits = {"chosen_index": 4}
    result = run(stage.apply_edits(ctx))
    doc = read_doc(folder)
    assert doc["chosen_index"] == 4 and doc["chosen_title"] == doc["variants"][4]["title"]
    assert project.title == doc["variants"][4]["title"]
    assert result.summary.startswith("Title chosen:")
    assert recent_titles(data_dir, "kind-ledger") == [project.title]

    ctx.edits = {"title_text": "  The Stranger Who   Stayed  "}
    run(stage.apply_edits(ctx))
    doc = read_doc(folder)
    assert project.title == "The Stranger Who Stayed" and doc["chosen_index"] is None
    assert recent_titles(data_dir, "kind-ledger") == ["The Stranger Who Stayed"]

    ctx.edits = {"chosen_index": 42}
    with pytest.raises(StageError, match="no title option number 43"):
        run(stage.apply_edits(ctx))
    ctx.edits = {}
    assert run(stage.apply_edits(ctx)) is None
    ctx.edits = {"chosen_index": "two"}
    with pytest.raises(StageError, match="not valid"):
        run(stage.apply_edits(ctx))


def test_reviewer_notes_and_framework_file_reach_the_prompt(app_env) -> None:
    frameworks = app_env.channels_dir / "kind-ledger" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "titles.md").write_text(
        "# House title rules\nAlways name the emotion.", encoding="utf-8"
    )
    channel = Channel.model_validate({
        "slug": "kind-ledger", "channel": {"name": "Kind Ledger"},
        "frameworks": [{"type": "title", "name": "House rules",
                        "path": "channels/kind-ledger/frameworks/titles.md"}],
    })
    folder = app_env.projects_dir / "notes"
    write_pick(folder)
    project = make_project(folder, project_id="p5")
    ctx = make_ctx(app_env, project=project, channel=channel, notes=["Shorter, please."])
    run(TitleStage().run(ctx))
    doc = read_doc(folder)
    assert doc["framework_source"] == "file" and doc["framework_name"] == "House rules"
    # The extracted text is cached in the app folder, not in the synced shared folder.
    assert [p.name for p in frameworks.iterdir()] == ["titles.md"]
    assert list((ctx.settings.app_data_dir / "cache" / "frameworks").glob("*.txt"))
    call = ctx.providers["llm"].calls[0]
    assert call["variables"]["notes"] == ["Shorter, please."]


def test_missing_research_title_is_a_plain_error(app_env) -> None:
    folder = app_env.projects_dir / "no-pick"
    (folder / "01_research").mkdir(parents=True)
    (folder / "01_research" / "pick.json").write_text('{"kind": "ai_pick"}', encoding="utf-8")
    project = make_project(folder, project_id="p6")
    with pytest.raises(StageError, match="Run research again"):
        load_source(folder, project)
    ctx = make_ctx(app_env, project=project)
    ctx.providers = {}
    with pytest.raises(StageError, match="No writing model"):
        run(TitleStage().run(ctx))


def test_ai_pick_without_research_files_is_a_plain_error(app_env) -> None:
    """Skipping a failed research step must not title the placeholder 'AI pick'."""
    folder = app_env.projects_dir / "no-research"
    folder.mkdir(parents=True)
    project = make_project(folder)
    project.title = "AI pick (research pending)"
    with pytest.raises(StageError, match="Research produced no picked video"):
        load_source(folder, project)
    ctx = make_ctx(app_env, project=project)
    with pytest.raises(StageError, match="Research produced no picked video"):
        run(TitleStage().run(ctx))
    assert ctx.providers["llm"].calls == []  # no money spent on a meaningless topic


def test_apply_edits_accepts_a_typed_title_when_no_options_exist(app_env) -> None:
    folder = app_env.projects_dir / "typed"
    write_pick(folder)
    project = make_project(folder, project_id="p7")
    ctx = make_ctx(app_env, project=project)
    ctx.edits = {"chosen_index": 0}
    with pytest.raises(StageError, match="has not produced its options"):
        run(TitleStage().apply_edits(ctx))
    ctx.edits = {"title_text": "A Title Typed By Hand"}
    result = run(TitleStage().apply_edits(ctx))
    assert project.title == "A Title Typed By Hand"
    assert result is not None and result.summary == "Title chosen: A Title Typed By Hand"
    doc = read_doc(folder)
    assert doc["variants"] == [] and doc["model"] == "person"
    assert doc["chosen_title"] == "A Title Typed By Hand" and doc["source_title"] == SOURCE_TITLE
    assert recent_titles(ctx.settings.app_data_dir, "kind-ledger") == ["A Title Typed By Hand"]
