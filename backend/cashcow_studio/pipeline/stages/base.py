"""The contract every stage implements (see docs/M1-M2-CONTRACT.md section 1).

A stage is a small object with a ``name`` and an async ``run``. It receives a
``StageContext`` (project, channel, settings, providers, progress callback) and returns a
``StageResult``. It must write its output files into the project folder before returning,
so a person can inspect or replace them between stages.

How the engine (``pipeline/engine.py``) uses a stage:

* ``ctx.project`` is the live ``Project`` object; the engine saves it to ``job.json`` right
  after ``run`` returns, so a stage may set ``ctx.project.title`` (the research stage sets it
  to the picked video's title, the title stage to the approved title).
* ``ctx.progress(message, pct)`` may be called from any thread; it is broadcast on the
  WebSocket as ``stage.progress``.
* ``ctx.notes`` holds the reviewer's notes (from redo / approve); add them to the prompt.
* ``StageResult.needs_review_payload`` is saved to ``<stage dir>/review_payload.json`` and
  served by ``GET /api/projects/{id}/stage/{stage}``. Put ``"cost_kind": "llm"|"voice"|"images"``
  in it when the cost belongs to another bucket than ``llm_usd`` (the default).
* Optional hook ``apply_edits(ctx)`` (see :class:`EditableStage`): when a reviewer approves
  with ``edits``, the engine calls it with ``ctx.edits`` filled instead of ``run``. Without
  the hook the engine stores the edits in ``<stage dir>/edits.json`` and, for the title stage,
  copies ``title_text`` / the ``chosen_index`` variant into ``project.title``.
* Raise ``StageError`` with a plain-English message to fail the stage, ``GateBlocked`` when a
  quality gate blocks it, ``NotImplementedStage`` when the runner is not built yet (the engine
  then marks the stage ``awaiting_manual`` so the project can be advanced by hand).
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ...models.channel import Channel
from ...models.project import Project, StageName

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...config import Settings

ProgressCallback = Callable[[str, float | None], Awaitable[None] | None]


class StageError(Exception):
    """A stage failed for a reason the user should read (plain English).

    ``cost_usd`` / ``cost_kind`` carry the money already spent before the failure (model
    calls that happened before a gate blocked the stage) so the engine can still book it.
    """

    def __init__(
        self, message: str = "", *, cost_usd: float = 0.0, cost_kind: str = "llm"
    ) -> None:
        super().__init__(message)
        self.cost_usd = float(cost_usd or 0.0)
        self.cost_kind = cost_kind or "llm"


class NotImplementedStage(StageError):
    """The stage exists in the state machine but its runner arrives in a later milestone."""


class GateBlocked(StageError):
    """A quality gate blocked the stage; ``reasons`` are shown to the reviewer."""

    def __init__(
        self, reasons: list[str], *, cost_usd: float = 0.0, cost_kind: str = "llm"
    ) -> None:
        super().__init__(
            "; ".join(reasons) or "A quality gate blocked this stage.",
            cost_usd=cost_usd,
            cost_kind=cost_kind,
        )
        self.reasons = reasons


@dataclass
class StageContext:
    project: Project
    channel: Channel
    settings: Settings
    folder: Path
    """Absolute project folder (``project.folder``)."""
    providers: dict[str, Any] = field(default_factory=dict)
    """Keys: ``llm``, ``research``, ``image``, ``voice`` (see providers/registry.py)."""
    notes: list[str] = field(default_factory=list)
    """Reviewer notes from a ``redo``; stages add them to their prompts."""
    edits: dict[str, Any] = field(default_factory=dict)
    """Stage-specific edits submitted with an approval or a redo (see the contract)."""
    progress: ProgressCallback = lambda message, pct=None: None
    cancel: threading.Event = field(default_factory=threading.Event)
    """Set by the engine when the project is archived or the app shuts down while the stage
    runs. Work that happens in a worker thread checks it between steps and before writing
    files, so a cancelled run never recreates an archived folder."""

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def stage_dir(self, stage: StageName) -> Path:
        """``01_research`` ... ``08_export`` inside the project folder (created on demand)."""
        path = self.folder / STAGE_DIRS[stage]
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def report(self, message: str, pct: float | None = None) -> None:
        result = self.progress(message, pct)
        if result is not None:
            await result


@dataclass
class StageResult:
    outputs: list[Path] = field(default_factory=list)
    summary: str = ""
    cost_usd: float = 0.0
    needs_review_payload: dict[str, Any] = field(default_factory=dict)
    """What the review screen shows for this stage (JSON-serialisable)."""
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    """``[{id, title, severity, passed, detail}]`` from the policy gates."""


class Stage(Protocol):
    name: StageName

    async def run(self, ctx: StageContext) -> StageResult: ...


class EditableStage(Stage, Protocol):
    """A stage that can apply the reviewer's approval ``edits`` without re-running.

    ``ctx.edits`` holds the stage-specific edits from the contract (title: ``chosen_index`` /
    ``title_text``; script: ``script_md`` / ``locked_paragraph_ids``; storyboard:
    ``storyboard``; research: ``video_id``). Return a ``StageResult`` to replace the saved
    review payload and summary, or ``None`` to keep them.
    """

    async def apply_edits(self, ctx: StageContext) -> StageResult | None: ...


STAGE_ORDER: tuple[StageName, ...] = (
    StageName.research,
    StageName.title,
    StageName.script,
    StageName.storyboard,
    StageName.voice,
    StageName.images,
    StageName.edit,
    StageName.export,
)

STAGE_DIRS: dict[StageName, str] = {
    StageName.research: "01_research",
    StageName.title: "02_title",
    StageName.script: "03_script",
    StageName.storyboard: "04_storyboard",
    StageName.voice: "05_voice",
    StageName.images: "06_images",
    StageName.edit: "07_edit",
    StageName.export: "08_export",
}
