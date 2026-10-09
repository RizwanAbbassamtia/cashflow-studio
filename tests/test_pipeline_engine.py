"""Pipeline engine state machine: auto / review / manual gates, approve, redo, skip, failure,
resume, the parallel limit, costs and provenance. Two tiny fake stages stand in for the real
research and title stages; nothing touches the network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from cashcow_studio.config import Settings
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectCreate, ProjectSource, StageName
from cashcow_studio.pipeline import engine as engine_module
from cashcow_studio.pipeline.engine import (
    InvalidProjectRequest,
    InvalidTransition,
    PipelineEngine,
    ProjectBusy,
)
from cashcow_studio.pipeline.stages.base import (
    GateBlocked,
    NotImplementedStage,
    StageContext,
    StageError,
    StageResult,
)
from cashcow_studio.storage.channel_store import ChannelStore
from conftest import AppEnv, channel_payload

SLUG = "kind-ledger"


class FakeStage:
    """A stage that writes one file and can be told to fail in different ways."""

    def __init__(
        self,
        name: str,
        *,
        cost: float = 0.0,
        cost_kind: str | None = None,
        behaviours: list[str] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.name = StageName(name)
        self.cost = cost
        self.cost_kind = cost_kind
        self.behaviours = list(behaviours or [])
        self.gate = gate
        self.calls: list[StageContext] = []
        self.titles_seen: list[str] = []

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls.append(ctx)
        self.titles_seen.append(ctx.project.title)
        if self.gate is not None:
            await self.gate.wait()
        behaviour = self.behaviours.pop(0) if self.behaviours else "ok"
        if behaviour == "error":
            raise StageError("The model returned an empty answer.")
        if behaviour == "crash":
            raise RuntimeError("list index out of range")
        if behaviour == "not_implemented":
            raise NotImplementedStage("later")
        if behaviour == "blocked":
            raise GateBlocked(["Too similar to the source title"])
        await ctx.report(f"{self.name.value} working", 50)
        out = ctx.stage_dir(self.name) / f"{self.name.value}.json"
        out.write_text(json.dumps({"notes": ctx.notes, "attempt": len(self.calls)}))
        if self.name == StageName.research:
            ctx.project.title = "Picked video title"
        payload: dict[str, Any] = {"stage": self.name.value, "attempt": len(self.calls)}
        if self.cost_kind:
            payload["cost_kind"] = self.cost_kind
        return StageResult(
            outputs=[out],
            summary=f"{self.name.value} finished",
            cost_usd=self.cost,
            needs_review_payload=payload,
            gate_results=[{"id": "g1", "title": "ok", "severity": "warn", "passed": True}],
        )


class EditableFakeStage(FakeStage):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.edits_seen: list[dict[str, Any]] = []

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        self.edits_seen.append(dict(ctx.edits))
        ctx.project.title = ctx.edits.get("title_text", ctx.project.title)
        return StageResult(summary="edited", needs_review_payload={"edited": True})


class BlockedEditsStage(FakeStage):
    """Its edits always fail a blocking check (a pasted competitor transcript, say)."""

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        return StageResult(
            summary="edited",
            needs_review_payload={"edited": True},
            gate_results=[{
                "id": "script.ngram_source", "title": "Original wording", "severity": "block",
                "passed": False, "detail": "90% of 8-word sequences copy the competitor",
            }],
        )


@pytest.fixture
def settings(app_env: AppEnv) -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


@pytest.fixture
def channel(settings: Settings) -> Channel:
    store = ChannelStore(settings.resolved_shared_dir)
    body = channel_payload("Kind Ledger") | {"slug": SLUG}
    return store.create(Channel.model_validate(body))


def make_engine(settings: Settings, *stages: Any, max_parallel: int = 2) -> PipelineEngine:
    engine = PipelineEngine(settings, max_parallel=max_parallel)
    for stage in stages:
        engine.register(stage)
    return engine


def create(
    engine: PipelineEngine,
    channel: Channel,
    source: dict[str, Any] | None = None,
    **modes: str,
) -> Project:
    body = ProjectCreate(
        channel_slug=channel.slug,
        format="long",
        source=ProjectSource.model_validate(source or {"kind": "ai_pick"}),
        stage_mode_overrides={StageName(k): v for k, v in modes.items()},  # type: ignore[misc]
    )
    return engine.create_project(body, channel)


async def settle(engine: PipelineEngine, *project_ids: str, timeout: float = 5.0) -> None:
    """Wait until the background runs for these projects have finished."""
    for project_id in project_ids:
        task = engine.tasks.get(project_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)
    await asyncio.sleep(0)


def run_async(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(coro_factory())


def statuses(project: Project) -> dict[str, str]:
    return {name.value: state.status for name, state in project.stages.items()}


ALL_AUTO = {name.value: "auto" for name in StageName}


# Auto / review / manual ------------------------------------------------------------------


def test_auto_mode_runs_registered_stages_and_parks_at_the_first_unbuilt_one(
    settings: Settings, channel: Channel
) -> None:
    research = FakeStage("research", cost=0.01)
    title = FakeStage("title", cost=0.02, cost_kind="voice")

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **ALL_AUTO)
        assert project.current_stage == StageName.research
        assert statuses(project)["research"] == "pending"
        folder = Path(project.folder)
        assert (folder / "job.json").is_file()

        _, started = await engine.run(project.id)
        assert started
        await settle(engine, project.id)

        final = engine.get(project.id)
        assert statuses(final) == {
            "research": "done", "title": "done", "script": "awaiting_manual",
            "storyboard": "pending", "voice": "pending", "images": "pending",
            "edit": "pending", "export": "pending",
        }
        assert final.current_stage == StageName.script
        assert final.title == "Picked video title"  # set by the stage through ctx.project
        assert final.costs.llm_usd == pytest.approx(0.01)
        assert final.costs.voice_usd == pytest.approx(0.02)
        assert final.stages[StageName.title].summary == "title finished"
        assert final.stages[StageName.title].attempts == 1
        assert final.stages[StageName.title].gate_results[0]["id"] == "g1"
        # Research learned the title: the folder was renamed after it (files are the truth).
        assert not folder.exists()
        folder = Path(final.folder)
        assert folder.name.endswith("_kind-ledger_picked-video-title")
        assert final.topic_slug == "picked-video-title"
        assert (folder / "job.json").is_file() and (folder / "02_title" / "title.json").is_file()
        assert "Folder renamed" in " ".join(final.stages[StageName.research].history)
        payload = engine.stage_payload(project.id, StageName.title)
        assert payload["stage"] == "title" and payload["cost_kind"] == "voice"
        script_history = " ".join(final.stages[StageName.script].history)
        assert "not available" in script_history
        research_history = " ".join(final.stages[StageName.research].history)
        assert "Started (attempt 1" in research_history and "Finished in" in research_history
        assert "01_research/research.json" in research_history
        # A second run while parked at a manual gate continues past it.
        _, started = await engine.run(project.id)
        assert started
        await settle(engine, project.id)
        again = engine.get(project.id)
        assert statuses(again)["script"] == "done"
        assert statuses(again)["storyboard"] == "awaiting_manual"
        assert "Continued after a manual change" in " ".join(
            again.stages[StageName.script].history
        )
        # The event bus saw every save and the progress call.
        types = [event["type"] for event in engine.events.history]
        assert "project.update" in types and "stage.progress" in types
        progress = [e for e in engine.events.history if e["type"] == "stage.progress"]
        assert progress[0]["message"] == "research working" and progress[0]["pct"] == 50.0

    run_async(main)


def test_review_mode_waits_for_approval_then_continues(
    settings: Settings, channel: Channel
) -> None:
    research, title = FakeStage("research"), FakeStage("title")

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **(ALL_AUTO | {"title": "review"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        parked = engine.get(project.id)
        assert statuses(parked)["title"] == "awaiting_review"
        assert parked.current_stage == StageName.title
        # Run does not get past a review gate.
        _, started = await engine.run(project.id)
        assert not started
        assert statuses(engine.get(project.id))["title"] == "awaiting_review"

        approved = await engine.approve(project.id, StageName.title, by="Imran", notes="Nice")
        assert approved.stages[StageName.title].status == "approved"
        assert approved.stages[StageName.title].approved_by == "Imran"
        assert approved.stages[StageName.title].approved_at is not None
        assert approved.stages[StageName.title].notes == ["Nice"]
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["title"] == "done"
        assert statuses(final)["script"] == "awaiting_manual"
        assert "Approved by Imran" in " ".join(final.stages[StageName.title].history)
        assert len(title.calls) == 1  # approving never re-runs the stage

    run_async(main)


def test_manual_mode_waits_for_files_and_run_continues(
    settings: Settings, channel: Channel
) -> None:
    research, title = FakeStage("research"), FakeStage("title")

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **(ALL_AUTO | {"research": "manual"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        parked = engine.get(project.id)
        assert statuses(parked)["research"] == "awaiting_manual"
        assert research.calls == []
        assert "set to manual" in " ".join(parked.stages[StageName.research].history)
        # The person drops the files in 01_research and presses Run.
        (Path(project.folder) / "01_research" / "pick.json").write_text("{}")
        await engine.run(project.id)
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["research"] == "done"
        assert statuses(final)["title"] == "done"
        assert research.calls == []
        assert len(title.calls) == 1

    run_async(main)


def test_own_topic_skips_research_and_titles_the_project(
    settings: Settings, channel: Channel
) -> None:
    research, title = FakeStage("research"), FakeStage("title")

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(
            engine, channel, {"kind": "own_topic", "topic_text": "  Why kindness pays off  "},
            **ALL_AUTO,
        )
        assert project.stages[StageName.research].status == "skipped"
        assert project.current_stage == StageName.title
        assert project.title == "Why kindness pays off"
        assert project.topic_slug == "why-kindness-pays-off"
        assert Path(project.folder).name.endswith("_kind-ledger_why-kindness-pays-off")
        await engine.run(project.id)
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert research.calls == []
        assert title.titles_seen == ["Why kindness pays off"]
        assert statuses(final)["title"] == "done"

    run_async(main)


# Redo / skip / failure / resume ----------------------------------------------------------


def test_redo_reruns_with_notes_and_resets_later_stages(
    settings: Settings, channel: Channel
) -> None:
    research, title, script = FakeStage("research"), FakeStage("title"), FakeStage("script")

    async def main() -> None:
        engine = make_engine(settings, research, title, script)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["storyboard"] == "awaiting_manual"

        redone = await engine.redo(project.id, StageName.title, by="Imran", notes="Punchier")
        assert redone.stages[StageName.title].status == "pending"
        assert redone.stages[StageName.script].status == "pending"
        assert redone.stages[StageName.research].status == "done"  # earlier stages keep
        assert redone.current_stage == StageName.title
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert final.stages[StageName.title].attempts == 2
        assert final.stages[StageName.title].notes == ["Punchier"]
        assert title.calls[-1].notes == ["Punchier"]
        assert len(script.calls) == 2  # re-run because its input changed
        assert statuses(final)["storyboard"] == "awaiting_manual"
        assert "Reset because the title step was redone" in " ".join(
            final.stages[StageName.script].history
        )
        written = json.loads((Path(final.folder) / "02_title" / "title.json").read_text())
        assert written["notes"] == ["Punchier"]

    run_async(main)


def test_redo_carries_edits_to_the_next_run(settings: Settings, channel: Channel) -> None:
    """``redo(edits=...)`` reaches the stage as ``ctx.edits`` once, then is cleared."""
    research, title = FakeStage("research"), FakeStage("title")

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        assert title.calls[-1].edits == {}

        redone = await engine.redo(
            project.id, StageName.title, by="Imran", notes="Keep the hook",
            edits={"locked_paragraph_ids": ["p-01-01"]},
        )
        assert redone.stages[StageName.title].pending_edits == {
            "locked_paragraph_ids": ["p-01-01"]
        }
        assert "Redo keeps these edits: locked_paragraph_ids" in " ".join(
            redone.stages[StageName.title].history
        )
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert title.calls[-1].edits == {"locked_paragraph_ids": ["p-01-01"]}
        assert title.calls[-1].notes == ["Keep the hook"]
        assert final.stages[StageName.title].pending_edits == {}  # used up by the run

    run_async(main)


def test_skip_marks_the_stage_and_moves_on(settings: Settings, channel: Channel) -> None:
    research, title, script = FakeStage("research"), FakeStage("title"), FakeStage("script")

    async def main() -> None:
        engine = make_engine(settings, research, title, script)
        project = create(engine, channel, **(ALL_AUTO | {"title": "review"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        skipped = await engine.skip(project.id, StageName.title, by="Imran", notes="Reuse old")
        assert skipped.stages[StageName.title].status == "skipped"
        assert skipped.stages[StageName.title].notes == ["Reuse old"]
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["script"] == "done"
        assert len(script.calls) == 1

    run_async(main)


def test_failures_are_plain_english_and_run_retries(
    settings: Settings, channel: Channel
) -> None:
    research = FakeStage("research")
    title = FakeStage("title", behaviours=["error", "crash", "blocked", "ok"])

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        failed = engine.get(project.id)
        state = failed.stages[StageName.title]
        assert state.status == "failed"
        assert state.error == "The model returned an empty answer."
        assert state.attempts == 1
        assert failed.current_stage == StageName.title

        await engine.run(project.id)
        await settle(engine, project.id)
        crashed = engine.get(project.id).stages[StageName.title]
        assert crashed.status == "failed"
        assert crashed.attempts == 2
        assert "unexpected problem" in (crashed.error or "")
        assert "list index out of range" in (crashed.error or "")

        await engine.run(project.id)
        await settle(engine, project.id)
        blocked = engine.get(project.id).stages[StageName.title]
        assert blocked.status == "failed"
        assert "quality check" in (blocked.error or "")
        assert blocked.gate_results[0]["passed"] is False
        assert blocked.gate_results[0]["detail"] == "Too similar to the source title"

        await engine.run(project.id)
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert final.stages[StageName.title].status == "done"
        assert final.stages[StageName.title].attempts == 4
        assert final.stages[StageName.title].error is None
        assert final.stages[StageName.title].gate_results[0]["id"] == "g1"
        assert "Trying again after a failure" in " ".join(final.stages[StageName.title].history)

    run_async(main)


def test_failed_stage_can_be_approved_as_an_override(
    settings: Settings, channel: Channel
) -> None:
    research, title = FakeStage("research"), FakeStage("title")
    script = FakeStage("script", behaviours=["blocked"])

    async def main() -> None:
        engine = make_engine(settings, research, title, script)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["script"] == "failed"
        await engine.approve(project.id, StageName.script, by="Imran", notes="Fine as is")
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["script"] == "done"
        assert statuses(final)["storyboard"] == "awaiting_manual"

    run_async(main)


def test_failed_title_needs_a_chosen_or_typed_title(settings: Settings, channel: Channel) -> None:
    """Approving a blocked title without a choice would send the competitor's own title on."""
    research = FakeStage("research")
    title = FakeStage("title", behaviours=["blocked"])

    async def main() -> None:
        engine = make_engine(settings, research, title)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["title"] == "failed"
        with pytest.raises(InvalidTransition, match="Choose one of the options"):
            await engine.approve(project.id, StageName.title, by="Imran", notes="Fine as is")
        assert engine.get(project.id).title == "Picked video title"
        approved = await engine.approve(
            project.id, StageName.title, by="Imran", edits={"title_text": "My own title"}
        )
        assert approved.title == "My own title"
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["title"] == "done"

    run_async(main)


def test_money_spent_before_a_gate_blocks_is_still_booked(
    settings: Settings, channel: Channel
) -> None:
    class CostlyBlockedStage(FakeStage):
        async def run(self, ctx: StageContext) -> StageResult:
            self.calls.append(ctx)
            raise GateBlocked(["Too similar to the source title"], cost_usd=1.0)

    async def main() -> None:
        engine = make_engine(settings, FakeStage("research"), CostlyBlockedStage("title"))
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["title"] == "failed"
        assert final.costs.llm_usd == pytest.approx(1.0)
        assert "Cost $1.0000" in " ".join(final.stages[StageName.title].history)

    run_async(main)


def test_research_cannot_be_skipped_for_a_competitor_video(
    settings: Settings, channel: Channel
) -> None:
    async def main() -> None:
        engine = make_engine(settings, FakeStage("research"))
        project = create(engine, channel, research="review")
        with pytest.raises(InvalidTransition, match="cannot be skipped"):
            await engine.skip(project.id, StageName.research, by="Imran", notes="x")
        assert statuses(engine.get(project.id))["research"] == "pending"

    run_async(main)


def test_not_implemented_stage_becomes_awaiting_manual(
    settings: Settings, channel: Channel
) -> None:
    research = FakeStage("research")
    voice_like_title = FakeStage("title", behaviours=["not_implemented"])

    async def main() -> None:
        engine = make_engine(settings, research, voice_like_title)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        await settle(engine, project.id)
        parked = engine.get(project.id)
        assert statuses(parked)["title"] == "awaiting_manual"
        assert parked.stages[StageName.title].error is None
        assert "not available" in parked.stages[StageName.title].summary
        approved = await engine.approve(project.id, StageName.title, by="Imran")
        assert approved.stages[StageName.title].status == "approved"
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["title"] == "done"

    run_async(main)


def test_interrupted_run_is_reported_as_failed_on_next_load(
    settings: Settings, channel: Channel
) -> None:
    async def main() -> None:
        engine = make_engine(settings, FakeStage("research"))
        project = create(engine, channel, **ALL_AUTO)
        # Simulate the app closing mid-stage: job.json says "running" but nothing runs.
        project.stages[StageName.research].status = "running"
        engine.store.save(project)
        loaded = engine.get(project.id)
        assert loaded.stages[StageName.research].status == "failed"
        assert loaded.stages[StageName.research].error == engine_module.INTERRUPTED_MESSAGE
        # Run picks it up again.
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["research"] == "done"

    run_async(main)


# Parallel limit and busy projects ---------------------------------------------------------


def test_at_most_max_parallel_projects_run_at_once(settings: Settings, channel: Channel) -> None:
    gate = asyncio.Event()
    research = FakeStage("research", gate=gate)

    async def main() -> None:
        engine = make_engine(settings, research, max_parallel=2)
        projects = [create(engine, channel, **ALL_AUTO) for _ in range(3)]
        for project in projects:
            await engine.run(project.id)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(research.calls) == 2:
                break
        states = [statuses(engine.get(p.id))["research"] for p in projects]
        assert sorted(states) == ["pending", "running", "running"]
        assert len(engine.tasks) == 3
        assert len(research.calls) == 2  # the third waits for a free slot
        gate.set()
        await settle(engine, *[p.id for p in projects])
        assert all(statuses(engine.get(p.id))["research"] == "done" for p in projects)
        assert len(research.calls) == 3
        assert engine.tasks == {}

    run_async(main)


def test_busy_project_refuses_review_actions_but_accepts_mode_changes(
    settings: Settings, channel: Channel
) -> None:
    gate = asyncio.Event()
    research = FakeStage("research", gate=gate)

    async def main() -> None:
        engine = make_engine(settings, research)
        project = create(engine, channel, **ALL_AUTO)
        await engine.run(project.id)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if research.calls:
                break
        assert engine.is_busy(project.id)
        with pytest.raises(ProjectBusy):
            await engine.approve(project.id, StageName.research, by="Imran")
        with pytest.raises(ProjectBusy):
            await engine.redo(project.id, StageName.research, by="Imran", notes="x")
        with pytest.raises(ProjectBusy):
            await engine.skip(project.id, StageName.research, by="Imran")
        changed = await engine.set_mode(project.id, StageName.title, "manual")
        assert changed.stage_modes.title == "manual"
        _, started = await engine.run(project.id)
        assert not started  # already running
        gate.set()
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert final.stage_modes.title == "manual"  # survived the stage's own save
        assert statuses(final)["research"] == "done"
        assert statuses(final)["title"] == "awaiting_manual"

    run_async(main)


# Edits, transitions and create checks ------------------------------------------------------


def test_approve_edits_use_the_stage_hook_or_the_generic_title_fallback(
    settings: Settings, channel: Channel
) -> None:
    research = FakeStage("research")
    editable = EditableFakeStage("title")

    async def main() -> None:
        engine = make_engine(settings, research, editable)
        project = create(engine, channel, **(ALL_AUTO | {"title": "review"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        approved = await engine.approve(
            project.id, StageName.title, by="Imran", edits={"title_text": "Hand-written title"}
        )
        assert editable.edits_seen == [{"title_text": "Hand-written title"}]
        assert approved.title == "Hand-written title"
        assert engine.stage_payload(project.id, StageName.title) == {"edited": True}
        await settle(engine, project.id)

        # A stage without the hook: the engine applies what it understands and keeps the rest.
        plain = make_engine(settings, FakeStage("research"), FakeStage("title"))
        other = create(plain, channel, **(ALL_AUTO | {"title": "review"}))
        await plain.run(other.id)
        await settle(plain, other.id)
        other_folder = Path(plain.get(other.id).folder)  # renamed after research
        title_file = other_folder / "02_title" / "title.json"
        title_file.write_text(
            json.dumps({"variants": [{"index": 0, "title": "A"}, {"index": 1, "title": "B"}]})
        )
        approved = await plain.approve(
            other.id, StageName.title, by="Imran", edits={"chosen_index": 1, "extra": "x"}
        )
        assert approved.title == "B"
        saved = json.loads((other_folder / "02_title" / "edits.json").read_text())
        assert saved == {"chosen_index": 1, "extra": "x"}
        await settle(plain, other.id)

    run_async(main)


def test_invalid_transitions_are_refused_with_a_reason(
    settings: Settings, channel: Channel
) -> None:
    async def main() -> None:
        engine = make_engine(settings, FakeStage("research"), FakeStage("title"))
        project = create(engine, channel, **(ALL_AUTO | {"script": "manual"}))
        with pytest.raises(InvalidTransition, match="still waiting to run"):
            await engine.approve(project.id, StageName.title, by="Imran")
        with pytest.raises(InvalidTransition, match="cannot be redone"):
            await engine.redo(project.id, StageName.title, by="Imran", notes="x")
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["script"] == "awaiting_manual"
        with pytest.raises(InvalidTransition, match="set to manual"):
            await engine.redo(project.id, StageName.script, by="Imran", notes="x")
        with pytest.raises(InvalidTransition, match="still waiting to run"):
            await engine.redo(project.id, StageName.storyboard, by="Imran", notes="x")
        with pytest.raises(InvalidTransition, match="already done"):
            await engine.skip(project.id, StageName.title, by="Imran")
        # A manual step only counts as done once its file is really there.
        with pytest.raises(InvalidTransition, match="script.json"):
            await engine.approve(project.id, StageName.script, by="Imran")
        with pytest.raises(InvalidTransition, match="script.json"):
            await engine.run(project.id)
        assert statuses(engine.get(project.id))["script"] == "awaiting_manual"
        (Path(engine.get(project.id).folder) / "03_script" / "script.json").write_text("{}")
        # Past the manual script step, the unbuilt storyboard step parks and cannot be redone.
        await engine.approve(project.id, StageName.script, by="Imran")
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["storyboard"] == "awaiting_manual"
        with pytest.raises(InvalidTransition, match="not available"):
            await engine.redo(project.id, StageName.storyboard, by="Imran", notes="x")
        # Skipping a later stage in advance is fine.
        skipped = await engine.skip(project.id, StageName.export, by="Imran", notes="No export")
        assert skipped.stages[StageName.export].status == "skipped"
        await settle(engine, project.id)

    run_async(main)


def test_create_checks_the_source_and_the_channel_format(
    settings: Settings, channel: Channel
) -> None:
    engine = make_engine(settings)
    with pytest.raises(InvalidProjectRequest, match="Type the topic"):
        create(engine, channel, {"kind": "own_topic", "topic_text": "   "})
    with pytest.raises(InvalidProjectRequest, match="Choose a video"):
        create(engine, channel, {"kind": "manual_pick"})
    picked = create(engine, channel, {"kind": "manual_pick", "video_id": "dQw4w9WgXcQ"})
    assert picked.source.video_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert picked.topic_slug == "pick-dqw4w9wgxcq"
    assert picked.stage_modes.title == channel.stage_modes.title  # copied from the channel

    shorts_only = channel.model_copy(deep=True)
    shorts_only.channel.formats = "shorts"
    with pytest.raises(InvalidProjectRequest, match="only makes Shorts"):
        create(engine, shorts_only)


def test_edits_that_fail_a_blocking_check_need_an_override(
    settings: Settings, channel: Channel
) -> None:
    research, title, script = FakeStage("research"), FakeStage("title"), BlockedEditsStage("script")

    async def main() -> None:
        engine = make_engine(settings, research, title, script)
        project = create(engine, channel, **(ALL_AUTO | {"script": "review"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        assert statuses(engine.get(project.id))["script"] == "awaiting_review"
        with pytest.raises(InvalidTransition, match="still blocks"):
            await engine.approve(
                project.id, StageName.script, by="Imran", edits={"script_md": "copied text"}
            )
        parked = engine.get(project.id)
        assert statuses(parked)["script"] == "awaiting_review"  # not approved
        assert not engine.is_busy(project.id)
        # The edited result is saved so the review shows the failed check.
        assert parked.stages[StageName.script].gate_results[0]["passed"] is False
        assert engine.stage_payload(project.id, StageName.script) == {"edited": True}
        approved = await engine.approve(
            project.id, StageName.script, by="Imran",
            edits={"script_md": "copied text", "override_gates": True},
        )
        assert approved.stages[StageName.script].status == "approved"
        assert "overridden by Imran" in " ".join(approved.stages[StageName.script].history)
        await settle(engine, project.id)

    run_async(main)


def test_redo_with_locked_paragraph_ids_keeps_them_verbatim(
    settings: Settings, channel: Channel
) -> None:
    """The real script stage through the engine: locks sent with the redo survive it."""
    from cashcow_studio.llm import MockLLMClient
    from cashcow_studio.pipeline.stages.script import ScriptStage

    research, title = FakeStage("research"), FakeStage("title")

    async def main() -> None:
        engine = PipelineEngine(
            settings, providers={"llm": MockLLMClient(app_data_dir=settings.app_data_dir)}
        )
        for stage in (research, title, ScriptStage()):
            engine.register(stage)
        project = create(engine, channel, **(ALL_AUTO | {"script": "review"}))
        await engine.run(project.id)
        await settle(engine, project.id)
        parked = engine.get(project.id)
        assert statuses(parked)["script"] == "awaiting_review"
        script_path = Path(parked.folder) / "03_script" / "script.json"
        before = json.loads(script_path.read_text(encoding="utf-8"))
        paragraphs = [p for s in before["sections"] for p in s["paragraphs"]]
        assert not any(p["locked"] for p in paragraphs)
        keep = before["sections"][1]["paragraphs"][0]  # first of its section: stable id

        await engine.redo(
            project.id, StageName.script, by="Imran", notes="Make it punchier",
            edits={"locked_paragraph_ids": [keep["id"]]},
        )
        await settle(engine, project.id)
        final = engine.get(project.id)
        assert statuses(final)["script"] == "awaiting_review"
        assert final.stages[StageName.script].attempts == 2
        after = json.loads(script_path.read_text(encoding="utf-8"))
        kept = [p for s in after["sections"] for p in s["paragraphs"] if p["locked"]]
        assert [(p["id"], p["text"]) for p in kept] == [(keep["id"], keep["text"])]
        call = next(c for c in reversed(engine.providers["llm"].calls) if c["task"] == "script")
        assert call["variables"]["locked_paragraphs"][0]["id"] == keep["id"]
        assert call["variables"]["notes"] == ["Make it punchier"]

    run_async(main)
