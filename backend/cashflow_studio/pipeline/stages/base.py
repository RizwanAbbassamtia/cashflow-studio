"""The contract every stage implements (see docs/M1-M2-CONTRACT.md section 1).

A stage is a small object with a ``name`` and an async ``run``. It receives a
``StageContext`` (project, channel, settings, providers, progress callback) and returns a
``StageResult``. It must write its output files into the project folder before returning,
so a person can inspect or replace them between stages.
"""

from __future__ import annotations

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
    """A stage failed for a reason the user should read (plain English)."""


class NotImplementedStage(StageError):
    """The stage exists in the state machine but its runner arrives in a later milestone."""


class GateBlocked(StageError):
    """A quality gate blocked the stage; ``reasons`` are shown to the reviewer."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons) or "A quality gate blocked this stage.")
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
    """Stage-specific edits submitted with an approval (see the contract)."""
    progress: ProgressCallback = lambda message, pct=None: None

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
