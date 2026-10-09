"""The pipeline engine: creates projects and moves them through the eight stages.

State machine (docs/M1-M2-CONTRACT.md section 1). Each stage has a mode and a status::

    mode auto    pending -> running -> done                      (continues by itself)
    mode review  pending -> running -> awaiting_review -> approved -> done
    mode manual  pending -> awaiting_manual -> done              (a person supplies the files)
    any mode     running -> failed                               (Run retries, attempts += 1)
    not built    pending -> awaiting_manual                      (NotImplementedStage or no runner)

A person acts with ``approve`` (awaiting_review / awaiting_manual / failed -> approved),
``redo`` (re-run the stage with notes; later stages are reset), ``skip`` and ``set_mode``.
``run`` resumes a project: it retries a failed stage, continues past an ``awaiting_manual``
stage (the files were supplied by hand), and otherwise advances until the next gate or the
end. Every change is written to ``job.json`` first and then broadcast as ``project.update``.

Runs happen in one asyncio task per project, at most ``max_parallel`` at a time; the tasks
are tracked in :attr:`PipelineEngine.tasks` (reachable as ``app.state.engine.tasks``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..llm.client import LLMError
from ..models.channel import Channel, StageMode
from ..models.project import (
    Project,
    ProjectCreate,
    ProjectSource,
    ProjectSummary,
    StageModes,
    StageName,
    StageState,
    StageStatus,
)
from ..research.cache import ResearchCache
from ..storage.channel_store import VIDEO_ID_RE, ChannelNotFound, ChannelStore
from ..storage.project_store import ProjectNotFound, ProjectStore, topic_slug
from ..storage.settings_store import atomic_write_text
from .events import EventBus
from .jobs import JobRegistry
from .jobs import registry as default_job_registry
from .stages.base import (
    STAGE_DIRS,
    STAGE_ORDER,
    GateBlocked,
    NotImplementedStage,
    Stage,
    StageContext,
    StageError,
    StageResult,
)

log = logging.getLogger(__name__)

DEFAULT_MAX_PARALLEL = 2
REVIEW_PAYLOAD_FILE = "review_payload.json"
EDITS_FILE = "edits.json"
MAX_HISTORY = 100

FINISHED: frozenset[StageStatus] = frozenset({"done", "skipped"})
GATES: frozenset[StageStatus] = frozenset({"awaiting_review", "awaiting_manual", "failed"})
APPROVABLE = GATES
REDOABLE: frozenset[StageStatus] = frozenset(
    {"awaiting_review", "awaiting_manual", "failed", "done"}
)
SKIPPABLE: frozenset[StageStatus] = frozenset(
    {"pending", "awaiting_review", "awaiting_manual", "failed"}
)

COST_FIELDS = {
    "llm": "llm_usd",
    "voice": "voice_usd",
    "image": "images_usd",
    "images": "images_usd",
}

INTERRUPTED_MESSAGE = (
    "Cashflow Studio was closed while this step was running. Press Run to try again."
)
NOT_AVAILABLE_MESSAGE = (
    "This step is not available in this version yet. Do it by hand in the project folder, "
    "then press Run to continue."
)
UNEXPECTED_MESSAGE = (
    "This step stopped because of an unexpected problem: {error}. Press Run to try again; if "
    "it keeps happening, check the log."
)
PLACEHOLDER_TOPICS = ("ai pick",)
"""Topics a project is created with before research knows the video; the folder is renamed
once the title is known (see :meth:`PipelineEngine._rename_folder`)."""
EXPECTED_OUTPUT: dict[StageName, str] = {
    StageName.research: "pick.json",
    StageName.title: "title.json",
    StageName.script: "script.json",
    StageName.storyboard: "storyboard.json",
}
"""The file a person must supply before a manual stage counts as done."""


class EngineError(Exception):
    """Base class; the message is plain English and safe to show to the user."""


class ProjectBusy(EngineError):
    """The project is running a stage right now; wait for it to reach a gate."""


class InvalidTransition(EngineError):
    """The requested action does not fit the stage's current status."""


class InvalidProjectRequest(EngineError):
    """The create request is incomplete or does not fit the channel (a 422)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(when: datetime | None = None) -> str:
    return (when or utc_now()).strftime("%Y-%m-%d %H:%M UTC")


class PipelineEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        events: EventBus | None = None,
        jobs: JobRegistry | None = None,
        providers: dict[str, Any] | None = None,
        stages: dict[StageName, Stage] | None = None,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
    ) -> None:
        self.settings = settings
        self.events = events or EventBus()
        self.jobs = jobs or default_job_registry
        self.providers: dict[str, Any] = providers or {}
        self.stages: dict[StageName, Stage] = dict(stages or {})
        self.max_parallel = max(1, int(max_parallel))
        self.tasks: dict[str, asyncio.Task[None]] = {}
        """Background run per project id (queued or active)."""
        self._active: dict[str, Project] = {}
        """Projects whose stage is executing right now, by id (the live Project object)."""
        self._contexts: dict[str, StageContext] = {}
        """The context of the stage running for each project, so archive/shutdown can set
        its cancel flag and stop work that runs in a worker thread."""
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # Wiring -------------------------------------------------------------------------------

    def register(self, stage: Stage) -> None:
        """Make a stage runnable. Unregistered stages are marked ``awaiting_manual``."""
        self.stages[StageName(stage.name)] = stage

    def set_max_parallel(self, value: int) -> None:
        """Change how many projects run at once (Settings > Models and providers).

        Runs that already hold a slot keep it; new runs use the new limit at once.
        """
        self.max_parallel = max(1, int(value))
        if self._semaphore is not None:
            self._semaphore = asyncio.Semaphore(self.max_parallel)

    @property
    def store(self) -> ProjectStore:
        # Built on demand so a projects folder changed in Settings takes effect immediately.
        return ProjectStore(self.settings.projects_dir, self.settings.app_data_dir)

    @property
    def channels(self) -> ChannelStore:
        return ChannelStore(self.settings.resolved_shared_dir)

    def _ensure_loop(self) -> asyncio.Semaphore:
        """The semaphore for the running loop (a new loop gets fresh bookkeeping)."""
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore = asyncio.Semaphore(self.max_parallel)
            self._loop = loop
            self.tasks.clear()
            self._active.clear()
            self._contexts.clear()
        return self._semaphore

    # Reads --------------------------------------------------------------------------------

    def get(self, project_id: str) -> Project:
        return self._load(project_id)

    def list(
        self, channel_slug: str | None = None, status: str | None = None
    ) -> list[ProjectSummary]:
        return self.store.list(channel_slug=channel_slug, status=status)

    def is_busy(self, project_id: str) -> bool:
        return project_id in self._active

    def stage_payload(self, project_id: str, stage: StageName) -> dict[str, Any]:
        """What the review screen shows for a stage; ``{}`` when the stage has not run."""
        project = self._load(project_id)
        path = Path(project.folder) / STAGE_DIRS[stage] / REVIEW_PAYLOAD_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {"payload": data}

    # Create -------------------------------------------------------------------------------

    def create_project(self, body: ProjectCreate, channel: Channel) -> Project:
        """Build the folder and ``job.json`` for a new video. Does not start the run."""
        source = _check_source(body.source)
        if channel.channel.formats not in ("both", body.format):
            raise InvalidProjectRequest(
                f"The channel '{channel.channel.name}' only makes "
                f"{'Shorts' if channel.channel.formats == 'shorts' else 'long videos'}."
            )
        now = utc_now()
        if source.kind == "own_topic":
            title = (source.topic_text or "").strip()
            topic = title
        elif source.kind == "manual_pick":
            title = f"Video {source.video_id}"
            topic = f"pick {source.video_id}"
        else:
            title = "AI pick (research pending)"
            topic = "ai pick"
        modes = StageModes.model_validate(channel.stage_modes.model_dump())
        for name, mode in body.stage_mode_overrides.items():
            setattr(modes, StageName(name).value, mode)
        store = self.store
        folder = store.new_folder(channel.slug, topic, now)
        project = Project(
            id=str(uuid.uuid4()),
            channel_slug=channel.slug,
            topic_slug=topic_slug(topic),
            title=title,
            format=body.format,
            language=channel.channel.language,
            created_at=now,
            updated_at=now,
            folder=str(folder),
            source=source,
            stage_modes=modes,
        )
        _history(project, StageName.research, f"Project created from {_source_label(source)}.")
        if source.kind == "own_topic":
            research = project.stages[StageName.research]
            research.status = "skipped"
            research.finished_at = now
            research.summary = "Not needed: the topic was typed in by hand."
            _history(project, StageName.research, "Skipped: own topic, no competitor video.")
            project.current_stage = StageName.title
        store.create(project)
        self.events.project_update(project)
        return project

    # Run / resume -------------------------------------------------------------------------

    async def run(self, project_id: str) -> tuple[Project, bool]:
        """Resume a project. Returns ``(project, started)``.

        ``started`` is False when the project is already running, is waiting for a review,
        or is complete.
        """
        self._ensure_loop()
        if project_id in self.tasks and not self.tasks[project_id].done():
            return self._load(project_id), False
        project = self._load(project_id)
        stage = _next_stage(project)
        if stage is None:
            return project, False
        state = project.stages[stage]
        if state.status == "failed":
            state.status = "pending"
            state.error = None
            _history(project, stage, "Trying again after a failure.")
        elif state.status == "awaiting_manual":
            missing = _missing_manual_output(project, stage)
            if missing:
                raise InvalidTransition(missing)
            _mark_done(
                project, stage, "Continued after a manual change: the files were supplied by hand."
            )
        elif state.status == "awaiting_review":
            return project, False
        project.current_stage = stage
        self._save(project)
        self._schedule(project.id)
        return project, True

    def _schedule(self, project_id: str) -> None:
        semaphore = self._ensure_loop()
        existing = self.tasks.get(project_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.get_running_loop().create_task(self._run_task(project_id, semaphore))
        self.tasks[project_id] = task

        def _cleanup(done: asyncio.Task[None]) -> None:
            if self.tasks.get(project_id) is done:
                self.tasks.pop(project_id, None)

        task.add_done_callback(_cleanup)

    async def _run_task(self, project_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                await self._advance(project_id)
            except asyncio.CancelledError:
                self._abandon(project_id, "Stopped: the run was cancelled.")
                raise
            except ProjectNotFound:
                log.info("Project %s disappeared during a run", project_id)
            except Exception:  # noqa: BLE001 - the task must never die silently
                log.exception("The run for project %s failed unexpectedly", project_id)
                self._abandon(project_id, UNEXPECTED_MESSAGE.format(error="internal error"))
            finally:
                self._active.pop(project_id, None)

    def _abandon(self, project_id: str, error: str) -> None:
        """Mark whatever is ``running`` as failed after a cancel or an internal error."""
        project = self._active.get(project_id)
        if project is None:
            return
        for stage in STAGE_ORDER:
            state = project.stages[stage]
            if state.status == "running":
                state.status = "failed"
                state.error = error
                state.finished_at = utc_now()
                _history(project, stage, error)
        try:
            self._save(project)
        except OSError:
            log.exception("Could not save project %s after abandoning its run", project_id)

    async def _advance(self, project_id: str) -> None:
        """Move through the stages until a gate, a failure or the end."""
        project = self._load(project_id)
        self._active[project_id] = project
        while True:
            stage = _next_stage(project)
            if stage is None:
                project.current_stage = StageName.export
                self._save(project)
                return
            project.current_stage = stage
            state = project.stages[stage]
            mode = project.stage_modes.for_stage(stage)
            if state.status in GATES:
                self._save(project)
                return
            if state.status == "approved":
                _mark_done(project, stage, "Done.")
                self._save(project)
                continue
            # pending (or an interrupted "running" that _load turned into failed earlier)
            if mode == "manual":
                state.status = "awaiting_manual"
                _history(
                    project,
                    stage,
                    f"Waiting for the files: this step is set to manual. Put them in "
                    f"{STAGE_DIRS[stage]} and press Run.",
                )
                self._save(project)
                return
            runner = self.stages.get(stage)
            if runner is None:
                state.status = "awaiting_manual"
                state.summary = NOT_AVAILABLE_MESSAGE
                _history(project, stage, NOT_AVAILABLE_MESSAGE)
                self._save(project)
                return
            await self._run_stage(project, stage, runner, mode)
            if project.stages[stage].status != "done":
                return

    async def _run_stage(
        self, project: Project, stage: StageName, runner: Stage, mode: StageMode
    ) -> None:
        state = project.stages[stage]
        state.status = "running"
        state.started_at = utc_now()
        state.finished_at = None
        state.error = None
        state.attempts += 1
        _history(project, stage, f"Started (attempt {state.attempts}, mode {mode}).")
        self._save(project)
        started = time.monotonic()
        try:
            channel = self.channels.get(project.channel_slug)
        except ChannelNotFound:
            self._fail(
                project,
                stage,
                f"The channel '{project.channel_slug}' no longer exists, so this step cannot "
                "run. Restore the channel or archive this project.",
            )
            return
        if stage == StageName.script:
            _warn_if_competitor_title(project)
        ctx = self._context(project, channel, stage)
        self._contexts[project.id] = ctx
        try:
            result = await runner.run(ctx)
        except NotImplementedStage:
            state.status = "awaiting_manual"
            state.finished_at = utc_now()
            state.summary = NOT_AVAILABLE_MESSAGE
            _history(project, stage, NOT_AVAILABLE_MESSAGE)
            self._save(project)
            return
        except GateBlocked as exc:
            state.gate_results = [
                {
                    "id": f"blocked-{index + 1}",
                    "title": "Quality gate",
                    "severity": "block",
                    "passed": False,
                    "detail": reason,
                }
                for index, reason in enumerate(exc.reasons)
            ]
            self._add_cost(project, stage, exc.cost_usd, exc.cost_kind)
            self._fail(project, stage, f"A quality check blocked this step: {exc}")
            return
        except StageError as exc:
            self._add_cost(project, stage, exc.cost_usd, exc.cost_kind)
            self._fail(project, stage, str(exc) or "This step failed.")
            return
        except LLMError as exc:
            self._fail(project, stage, str(exc) or "The writing model could not be used.")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any bug in a stage becomes a readable failure
            log.exception("Stage %s of project %s crashed", stage.value, project.id)
            self._fail(project, stage, UNEXPECTED_MESSAGE.format(error=_short(exc)))
            return
        finally:
            if self._contexts.get(project.id) is ctx:
                self._contexts.pop(project.id, None)
        self._record_result(project, stage, result)
        state.pending_edits = {}
        elapsed = time.monotonic() - started
        outputs = ", ".join(_relative(project, path) for path in result.outputs) or "no files"
        _history(project, stage, f"Finished in {elapsed:.1f} s. Wrote: {outputs}.")
        if stage == StageName.research:
            self._rename_folder(project)
        if mode == "review":
            state.status = "awaiting_review"
            state.finished_at = utc_now()
            _history(project, stage, "Waiting for a review.")
        else:
            _mark_done(project, stage, "Done (auto).")
        self._save(project)

    def _context(self, project: Project, channel: Channel, stage: StageName) -> StageContext:
        events = self.events
        project_id = project.id

        def progress(message: str, pct: float | None = None) -> None:
            events.stage_progress(project_id, stage.value, message, pct)

        return StageContext(
            project=project,
            channel=channel,
            settings=self.settings,
            folder=Path(project.folder),
            providers=self.providers,
            notes=list(project.stages[stage].notes),
            edits=dict(project.stages[stage].pending_edits),
            progress=progress,
        )

    def _rename_folder(self, project: Project) -> None:
        """After research: the folder gets the video's title instead of ``ai-pick``.

        Files are the truth, so the name on disk should say which video the folder holds.
        Runs between stages, when nothing writes into the folder; a folder that cannot be
        moved (a file open in another program) keeps its name and the run goes on.
        """
        if not project.title or project.source.kind == "own_topic":
            return
        placeholder = project.topic_slug in {topic_slug(t) for t in PLACEHOLDER_TOPICS}
        if not placeholder and not project.topic_slug.startswith("pick-"):
            return
        before = Path(project.folder).name
        try:
            renamed = self.store.rename(project, project.title)
        except Exception:  # noqa: BLE001 - a rename problem must never fail the stage
            log.exception("Could not rename the folder of project %s", project.id)
            return
        if renamed:
            _history(
                project,
                StageName.research,
                f"Folder renamed from {before} to {Path(project.folder).name}.",
            )

    def _fail(self, project: Project, stage: StageName, error: str) -> None:
        state = project.stages[stage]
        state.status = "failed"
        state.error = error
        state.finished_at = utc_now()
        _history(project, stage, f"Failed: {error}")
        self._save(project)

    def _record_result(self, project: Project, stage: StageName, result: StageResult) -> None:
        state = project.stages[stage]
        if result.summary:
            state.summary = result.summary
        state.gate_results = list(result.gate_results)
        payload = result.needs_review_payload or {}
        self._add_cost(project, stage, result.cost_usd, str(payload.get("cost_kind", "llm")))
        if payload:
            path = Path(project.folder) / STAGE_DIRS[stage] / REVIEW_PAYLOAD_FILE
            try:
                atomic_write_text(path, json.dumps(payload, indent=2, default=str) + "\n")
            except (OSError, TypeError, ValueError) as exc:
                log.warning("Could not save the review payload for %s: %s", stage.value, exc)

    def _add_cost(self, project: Project, stage: StageName, cost: float, kind: str) -> None:
        """Book money spent on a stage, also when the stage then failed a gate."""
        cost = float(cost or 0.0)
        if not cost:
            return
        field = COST_FIELDS.get(str(kind or "llm").lower(), "llm_usd")
        setattr(project.costs, field, round(getattr(project.costs, field) + cost, 6))
        _history(project, stage, f"Cost ${cost:.4f} ({field.removesuffix('_usd')}).")

    # Reviewer actions ---------------------------------------------------------------------

    async def approve(
        self,
        project_id: str,
        stage: StageName,
        by: str,
        notes: str | None = None,
        edits: dict[str, Any] | None = None,
    ) -> Project:
        project = self._load_for_change(project_id)
        state = project.stages[stage]
        if state.status not in APPROVABLE:
            raise InvalidTransition(
                f"The {stage.value} step cannot be approved while it is "
                f"{_status_label(state.status)}."
            )
        who = (by or "").strip() or "someone"
        if stage == StageName.title and state.status == "failed" and not _chooses_title(edits):
            raise InvalidTransition(
                "The title step did not produce a usable title, so there is nothing to approve "
                "yet. Choose one of the options, type a title, or redo the step with notes; "
                "the script must not be written under the competitor's own title."
            )
        if state.status == "awaiting_manual" and project.stage_modes.for_stage(stage) == "manual":
            missing = _missing_manual_output(project, stage)
            if missing:
                raise InvalidTransition(missing)
        if edits:
            # Busy while the edits are applied: a second click cannot start the same work
            # twice (the research hook reads the chosen video from YouTube).
            self._active[project.id] = project
            try:
                await self._apply_edits(project, stage, edits, who)
            finally:
                if self._active.get(project.id) is project:
                    self._active.pop(project.id, None)
        if notes and notes.strip():
            state.notes.append(notes.strip())
        state.pending_edits = {}
        state.status = "approved"
        state.approved_by = who
        state.approved_at = utc_now()
        state.error = None
        _history(project, stage, f"Approved by {who}.")
        project.current_stage = stage
        self._save(project)
        self._schedule(project.id)
        return project

    async def redo(
        self,
        project_id: str,
        stage: StageName,
        by: str,
        notes: str | None = None,
        edits: dict[str, Any] | None = None,
    ) -> Project:
        """Run the stage again with the notes added to the prompt.

        ``edits`` travel with the redo and reach the stage as ``ctx.edits`` on that run: the
        script stage keeps the paragraphs in ``locked_paragraph_ids`` word for word.
        """
        project = self._load_for_change(project_id)
        state = project.stages[stage]
        if state.status not in REDOABLE:
            raise InvalidTransition(
                f"The {stage.value} step cannot be redone while it is "
                f"{_status_label(state.status)}."
            )
        if project.stage_modes.for_stage(stage) == "manual":
            raise InvalidTransition(
                f"The {stage.value} step is set to manual, so there is nothing to redo. "
                "Switch it to auto or review first."
            )
        if stage not in self.stages:
            raise InvalidTransition(
                f"The {stage.value} step is not available in this version yet, so it cannot "
                "be redone."
            )
        who = (by or "").strip() or "someone"
        if notes and notes.strip():
            state.notes.append(notes.strip())
        state.pending_edits = dict(edits) if edits else {}
        state.status = "pending"
        state.error = None
        state.approved_by = None
        state.approved_at = None
        state.finished_at = None
        _history(
            project, stage, f"Redo requested by {who}" + (f": {notes.strip()}" if notes else ".")
        )
        if state.pending_edits:
            _history(
                project, stage, f"Redo keeps these edits: {', '.join(sorted(state.pending_edits))}."
            )
        later = False
        for name in STAGE_ORDER:
            if name == stage:
                later = True
                continue
            if later:
                _reset_later(project, name, stage)
        project.current_stage = stage
        self._save(project)
        self._schedule(project.id)
        return project

    async def skip(
        self, project_id: str, stage: StageName, by: str, notes: str | None = None
    ) -> Project:
        project = self._load_for_change(project_id)
        state = project.stages[stage]
        if state.status not in SKIPPABLE:
            raise InvalidTransition(
                f"The {stage.value} step cannot be skipped while it is "
                f"{_status_label(state.status)}."
            )
        if stage == StageName.research and project.source.kind != "own_topic":
            raise InvalidTransition(
                "Research cannot be skipped for a video that starts from a competitor's video: "
                "the title and script steps need the picked video. Redo research, choose a "
                "video, or start a new project from your own topic."
            )
        who = (by or "").strip() or "someone"
        if notes and notes.strip():
            state.notes.append(notes.strip())
        state.status = "skipped"
        state.error = None
        state.finished_at = utc_now()
        _history(project, stage, f"Skipped by {who}" + (f": {notes.strip()}" if notes else "."))
        self._save(project)
        self._schedule(project.id)
        return project

    async def set_mode(self, project_id: str, stage: StageName, mode: StageMode) -> Project:
        project = self._active.get(project_id) or self._load(project_id)
        setattr(project.stage_modes, stage.value, mode)
        _history(project, stage, f"Mode set to {mode}.")
        self._save(project)
        return project

    async def archive(self, project_id: str) -> Path:
        """Stop any run (including work in a worker thread) and move the folder to ``_archived``.

        The running stage's cancel flag is set first, so a research scan stops between
        provider calls and writes nothing more; the task is awaited so the thread has finished
        before the folder moves. The project's remembered pick is forgotten: no video was made.
        """
        ctx = self._contexts.get(project_id)
        if ctx is not None:
            ctx.cancel.set()
        task = self.tasks.get(project_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._active.pop(project_id, None)
        self._contexts.pop(project_id, None)
        project = self.store.get(project_id)
        destination = self.store.archive(project_id)
        try:
            ResearchCache(self.settings.app_data_dir).forget_project(
                project.channel_slug, project_id
            )
        except Exception:  # noqa: BLE001 - the archive succeeded; the index is best effort
            log.exception("Could not forget the research pick of project %s", project_id)
        self.events.publish(
            {
                "type": "project.update",
                "project_id": project_id,
                "status": "archived",
                "archived": True,
                "archived_to": str(destination),
            }
        )
        return destination

    async def shutdown(self) -> None:
        """Cancel every background run (used when the app or a test ends)."""
        for ctx in list(self._contexts.values()):
            ctx.cancel.set()
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.tasks.clear()
        self._active.clear()
        self._contexts.clear()

    # Edits --------------------------------------------------------------------------------

    async def _apply_edits(
        self, project: Project, stage: StageName, edits: dict[str, Any], who: str
    ) -> None:
        runner = self.stages.get(stage)
        hook = getattr(runner, "apply_edits", None)
        if runner is not None and callable(hook):
            try:
                channel = self.channels.get(project.channel_slug)
            except ChannelNotFound as exc:
                raise InvalidTransition(
                    f"The channel '{project.channel_slug}' no longer exists, so the edits "
                    "cannot be applied."
                ) from exc
            ctx = self._context(project, channel, stage)
            ctx.edits = dict(edits)
            try:
                result = await hook(ctx)
            except (StageError, LLMError) as exc:
                raise InvalidTransition(f"The edits could not be applied: {exc}") from exc
            if result is not None:
                self._record_result(project, stage, result)
            _history(project, stage, f"Edits applied by {who}: {', '.join(sorted(edits))}.")
            blocked = _blocking_details(result.gate_results if result is not None else [])
            if blocked and not edits.get("override_gates"):
                _history(
                    project, stage, "Not approved: a quality check blocks this step: "
                    + "; ".join(blocked)
                )
                self._save(project)
                raise InvalidTransition(
                    "Your edits are saved, but a quality check still blocks this step: "
                    + "; ".join(blocked)
                    + ". Change the text, or tick 'Approve anyway' to override the check."
                )
            if blocked:
                _history(project, stage, f"Blocking checks overridden by {who}.")
            return
        # No hook: keep the edits as a file and apply what the engine understands itself.
        path = Path(project.folder) / STAGE_DIRS[stage] / EDITS_FILE
        try:
            atomic_write_text(path, json.dumps(edits, indent=2, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            log.warning("Could not save the edits for %s: %s", stage.value, exc)
        applied = _apply_generic_edits(project, stage, edits)
        _history(
            project,
            stage,
            f"Edits saved by {who} to {STAGE_DIRS[stage]}/{EDITS_FILE}"
            + (f"; applied: {applied}." if applied else "."),
        )

    # Loading and saving -------------------------------------------------------------------

    def _load(self, project_id: str) -> Project:
        active = self._active.get(project_id)
        if active is not None:
            return active
        project = self.store.get(project_id)
        task = self.tasks.get(project_id)
        running = task is not None and not task.done()
        if not running:
            # A stage left "running" on disk means the app stopped mid-run.
            changed = False
            for stage in STAGE_ORDER:
                state = project.stages[stage]
                if state.status == "running":
                    state.status = "failed"
                    state.error = INTERRUPTED_MESSAGE
                    state.finished_at = utc_now()
                    _history(project, stage, INTERRUPTED_MESSAGE)
                    changed = True
            if changed:
                self._save(project)
        return project

    def _load_for_change(self, project_id: str) -> Project:
        if self.is_busy(project_id):
            raise ProjectBusy(
                "This project is running a step right now. Wait until it finishes, then try "
                "again."
            )
        return self._load(project_id)

    def _save(self, project: Project) -> None:
        self.store.save(project)
        self.events.project_update(project)


# Helpers ----------------------------------------------------------------------------------


def _check_source(source: ProjectSource) -> ProjectSource:
    if source.kind == "own_topic":
        if not (source.topic_text or "").strip():
            raise InvalidProjectRequest("Type the topic you want the video to be about.")
        return source.model_copy(update={"topic_text": source.topic_text.strip()})
    if source.kind == "manual_pick":
        video_id = (source.video_id or "").strip()
        if not video_id:
            raise InvalidProjectRequest("Choose a video from the candidates list first.")
        if not VIDEO_ID_RE.match(video_id):
            raise InvalidProjectRequest(
                "That is not a YouTube video id (11 letters, digits, '-' or '_'). Choose a "
                "video from the candidates list."
            )
        # The link is always built from the id: the project page renders it as a link.
        url = f"https://www.youtube.com/watch?v={video_id}"
        return source.model_copy(update={"video_id": video_id, "video_url": url})
    return source


def _chooses_title(edits: dict[str, Any] | None) -> bool:
    if not edits:
        return False
    if isinstance(edits.get("chosen_index"), int) and not isinstance(edits["chosen_index"], bool):
        return True
    return bool(str(edits.get("title_text") or "").strip())


def _blocking_details(gate_results: list[dict[str, Any]]) -> list[str]:
    return [
        str(r.get("detail") or r.get("title") or "a check failed")
        for r in gate_results
        if isinstance(r, dict) and r.get("severity") == "block" and not r.get("passed", True)
    ]


def _missing_manual_output(project: Project, stage: StageName) -> str | None:
    """Plain-English reason a manual stage is not done yet, or ``None`` when its file exists."""
    if project.stage_modes.for_stage(stage) != "manual":
        return None
    name = EXPECTED_OUTPUT.get(stage)
    if name is None:
        return None
    if (Path(project.folder) / STAGE_DIRS[stage] / name).is_file():
        return None
    return (
        f"The {stage.value} step is set to manual and {STAGE_DIRS[stage]}/{name} is not there "
        f"yet. Put {name} in {STAGE_DIRS[stage]} first, then press Run."
    )


def _warn_if_competitor_title(project: Project) -> None:
    """A script written under the competitor's own title is a copy; say so in the log."""
    if project.source.kind == "own_topic" or not project.title:
        return
    pick = _read_json(Path(project.folder) / STAGE_DIRS[StageName.research] / "pick.json")
    picked = str((pick or {}).get("title") or "").strip()
    if picked and picked.casefold() == project.title.strip().casefold():
        _history(
            project,
            StageName.script,
            "Warning: the project title is still the competitor's own title. The script is "
            "written under it; redo the title step to get original options.",
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _source_label(source: ProjectSource) -> str:
    if source.kind == "own_topic":
        return f"an own topic ('{source.topic_text}')"
    if source.kind == "manual_pick":
        return f"a hand-picked video ({source.video_id})"
    return "the AI pick"


def _next_stage(project: Project) -> StageName | None:
    for stage in STAGE_ORDER:
        if project.stages[stage].status not in FINISHED:
            return stage
    return None


def _mark_done(project: Project, stage: StageName, note: str) -> None:
    state = project.stages[stage]
    state.status = "done"
    state.error = None
    state.finished_at = utc_now()
    _history(project, stage, note)


def _reset_later(project: Project, stage: StageName, because: StageName) -> None:
    state = project.stages[stage]
    if state.status == "pending":
        return
    if state.status == "skipped" and stage == StageName.research:
        return  # an own-topic project never needs research
    state.status = "pending"
    state.error = None
    state.started_at = None
    state.finished_at = None
    state.approved_by = None
    state.approved_at = None
    state.summary = ""
    state.gate_results = []
    state.pending_edits = {}
    # The old review payload would otherwise show up for a stage that has not run again.
    payload = Path(project.folder) / STAGE_DIRS[stage] / REVIEW_PAYLOAD_FILE
    try:
        payload.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not remove the old review payload %s: %s", payload, exc)
    _history(project, stage, f"Reset because the {because.value} step was redone.")


def _history(project: Project, stage: StageName, text: str) -> None:
    state: StageState = project.stages[stage]
    state.history.append(f"{_stamp()}  {text}")
    if len(state.history) > MAX_HISTORY:
        del state.history[: len(state.history) - MAX_HISTORY]


def _relative(project: Project, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(project.folder).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name


def _short(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= 200 else text[:197] + "..."


def _status_label(status: StageStatus) -> str:
    return {
        "pending": "still waiting to run",
        "running": "running",
        "awaiting_review": "waiting for a review",
        "awaiting_manual": "waiting for files",
        "approved": "already approved",
        "done": "already done",
        "failed": "failed",
        "skipped": "skipped",
    }.get(status, status)


def _apply_generic_edits(project: Project, stage: StageName, edits: dict[str, Any]) -> str:
    """What the engine can do with edits when the stage has no ``apply_edits`` hook."""
    applied: list[str] = []
    if stage == StageName.title:
        text = edits.get("title_text")
        if isinstance(text, str) and text.strip():
            project.title = text.strip()
            applied.append("title_text")
        elif isinstance(edits.get("chosen_index"), int):
            chosen = _variant_title(project, int(edits["chosen_index"]))
            if chosen:
                project.title = chosen
                applied.append("chosen_index")
    elif stage == StageName.research:
        video_id = edits.get("video_id")
        if isinstance(video_id, str) and video_id.strip():
            video_id = video_id.strip()
            project.source = ProjectSource(
                kind="manual_pick",
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
            )
            applied.append("video_id")
    return ", ".join(applied)


def _variant_title(project: Project, index: int) -> str | None:
    """The title of variant ``index`` from ``title.json`` or, failing that, the review payload."""
    stage_dir = Path(project.folder) / STAGE_DIRS[StageName.title]
    for name in ("title.json", REVIEW_PAYLOAD_FILE):
        try:
            data = json.loads((stage_dir / name).read_text(encoding="utf-8"))
            variants = data.get("variants") or []
            for variant in variants:
                if isinstance(variant, dict) and variant.get("index") == index:
                    return str(variant.get("title") or "") or None
            if 0 <= index < len(variants) and isinstance(variants[index], dict):
                return str(variants[index].get("title") or "") or None
        except (OSError, ValueError, AttributeError, ValidationError):
            continue
    return None
