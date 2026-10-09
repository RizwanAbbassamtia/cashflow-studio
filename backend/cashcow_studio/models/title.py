"""Title stage data: what Claude returns, what ``02_title/title.json`` holds, and the edits a
reviewer may send with an approval. See docs/M1-M2-CONTRACT.md section 4.

The front end mirrors these in ``frontend/src/types/title.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TitleSourceKind = Literal["ai_pick", "manual_pick", "own_topic"]


# What Claude returns (kept free of numeric constraints so the JSON schema stays simple and
# stable; ranges are checked by the stage afterwards) --------------------------------------


class TitleVariantLLM(BaseModel):
    title: str
    formula: str = Field(description="the title pattern used, e.g. 'How X did Y without Z'")
    emotional_trigger: str = ""
    curiosity_trigger: str = ""
    hidden_gap: str = Field(default="", description="what the viewer does not know yet")
    viral_score: int = Field(description="1 (weak) to 10 (certain hit)")
    why_it_outperforms: str = ""
    keywords_kept: list[str] = []


class TitleLLMOutput(BaseModel):
    variants: list[TitleVariantLLM]
    recommended_index: int = Field(description="0-based index into variants")


# What the stage writes ---------------------------------------------------------------------


class TitleVariant(TitleVariantLLM):
    index: int
    similarity_to_source: float = Field(description="difflib ratio to the source title, 0-1")
    similarity_to_history: float = Field(description="highest ratio to a recent channel title")
    length: int
    flags: list[str] = Field(default=[], description="plain-English reasons a gate failed")

    @property
    def clean(self) -> bool:
        return not self.flags


class TitleSource(BaseModel):
    kind: TitleSourceKind
    title: str = Field(description="the competitor title, or the typed topic")
    video_id: str | None = None
    url: str | None = None
    channel_name: str | None = None
    views: int | None = None
    outlier_score: float | None = None


class TitleDoc(BaseModel):
    """``02_title/title.json``."""

    variants: list[TitleVariant]
    recommended_index: int | None = Field(
        description="null only when every option failed a gate (the stage then fails)"
    )
    source_title: str
    source: TitleSource
    generated_at: datetime
    model: str
    language: str = "English"
    format: str = "long"
    framework_source: Literal["file", "default"] = "default"
    framework_name: str | None = None
    history_compared: int = 0
    chosen_index: int | None = None
    chosen_title: str | None = None
    gate_results: list[dict[str, Any]] = []
    notes: list[str] = []
    schema_version: int = 1

    def variant(self, index: int) -> TitleVariant | None:
        for variant in self.variants:
            if variant.index == index:
                return variant
        return None


class TitleApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/title/approve``: pick a variant or type a title."""

    chosen_index: int | None = None
    title_text: str | None = Field(default=None, max_length=200)


class TitleReviewPayload(BaseModel):
    stage: Literal["title"] = "title"
    title: TitleDoc
    recent_titles: list[str] = []
    rules: dict[str, Any] = {}
