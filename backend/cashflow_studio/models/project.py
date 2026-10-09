"""Project (one video in production) and its per-stage state. Stored as ``job.json`` in the
project folder; indexed in SQLite. See docs/M1-M2-CONTRACT.md section 1.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .channel import Language, StageMode


class StageName(StrEnum):
    research = "research"
    title = "title"
    script = "script"
    storyboard = "storyboard"
    voice = "voice"
    images = "images"
    edit = "edit"
    export = "export"


StageStatus = Literal[
    "pending",
    "running",
    "awaiting_review",
    "awaiting_manual",
    "approved",
    "done",
    "failed",
    "skipped",
]

ProjectFormat = Literal["long", "shorts"]
SourceKind = Literal["ai_pick", "manual_pick", "own_topic"]


class ProjectSource(BaseModel):
    kind: SourceKind
    video_id: str | None = None
    video_url: str | None = None
    topic_text: str | None = None


class StageState(BaseModel):
    status: StageStatus = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    attempts: int = 0
    approved_by: str | None = None
    approved_at: datetime | None = None
    notes: list[str] = []
    summary: str = ""
    gate_results: list[dict] = []


class StageModes(BaseModel):
    research: StageMode = "review"
    title: StageMode = "review"
    script: StageMode = "review"
    storyboard: StageMode = "review"
    voice: StageMode = "auto"
    images: StageMode = "review"
    edit: StageMode = "review"
    export: StageMode = "review"

    def for_stage(self, stage: StageName) -> StageMode:
        return getattr(self, stage.value)


class ProjectCosts(BaseModel):
    llm_usd: float = 0.0
    voice_usd: float = 0.0
    images_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return round(self.llm_usd + self.voice_usd + self.images_usd, 4)


class Project(BaseModel):
    id: str
    channel_slug: str
    topic_slug: str
    title: str = ""
    format: ProjectFormat = "long"
    language: Language = "English"
    created_at: datetime
    updated_at: datetime
    folder: str
    source: ProjectSource
    stage_modes: StageModes = StageModes()
    stages: dict[StageName, StageState] = Field(
        default_factory=lambda: {s: StageState() for s in StageName}
    )
    costs: ProjectCosts = ProjectCosts()
    current_stage: StageName = StageName.research
    schema_version: int = 1


class ProjectSummary(BaseModel):
    id: str
    channel_slug: str
    title: str
    format: ProjectFormat
    language: Language
    current_stage: StageName
    status: StageStatus
    updated_at: datetime
    costs_total_usd: float = 0.0


class ProjectCreate(BaseModel):
    channel_slug: str
    format: ProjectFormat = "long"
    source: ProjectSource
    stage_mode_overrides: dict[StageName, StageMode] = {}
