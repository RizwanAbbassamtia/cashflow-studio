"""Storyboard data: ``04_storyboard/storyboard.json`` (:class:`StoryboardDoc`), the draft
Claude returns and the approval edits. See docs/M1-M2-CONTRACT.md section 6.

Rectangles are fractions of the frame ``[x, y, w, h]`` in 0-1 so they work for any resolution
and aspect; the edit stage multiplies by the output size.

The front end mirrors these in ``frontend/src/types/storyboard.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Aspect = Literal["16:9", "9:16"]
PopupPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]
MotionPreset = Literal[
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "hold", "slow_push"
]
ImageStatus = Literal["pending", "generated", "approved", "rejected"]

POPUP_POSITIONS: tuple[PopupPosition, ...] = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
)
MOTION_PRESETS: tuple[MotionPreset, ...] = (
    "zoom_in",
    "pan_right",
    "zoom_out",
    "pan_left",
    "slow_push",
    "pan_up",
    "pan_down",
    "hold",
)
MAX_POPUP_WORDS = 6


class ScenePopup(BaseModel):
    text: str | None = Field(default=None, description="at most 6 words; null = no popup")
    style: str = ""
    position: PopupPosition = "bottom-left"
    in_offset_s: float = 0.4
    out_offset_s: float = 3.5


class SceneMotion(BaseModel):
    preset: MotionPreset = "zoom_in"
    start_rect: list[float] = Field(default=[0.0, 0.0, 1.0, 1.0], min_length=4, max_length=4)
    end_rect: list[float] = Field(default=[0.1, 0.1, 0.8, 0.8], min_length=4, max_length=4)


class SceneTransition(BaseModel):
    type: str = Field(description="an xfade name from config/transitions.yaml")
    duration_s: float = 0.6


class SceneImage(BaseModel):
    path: str | None = None
    status: ImageStatus = "pending"
    qa: dict[str, Any] | None = None


class SceneLocks(BaseModel):
    image_prompt: bool = False
    popup: bool = False
    motion: bool = False
    transition_out: bool = False
    narration: bool = False

    @property
    def any(self) -> bool:
        return any(
            (self.image_prompt, self.popup, self.motion, self.transition_out, self.narration)
        )


class Scene(BaseModel):
    index: int
    sentence_ids: list[str]
    narration: str
    est_start_s: float = 0.0
    est_end_s: float = 0.0
    est_duration_s: float = 0.0
    image_prompt: str = Field(description="text-free; the stage prefixes the style guide")
    negative_prompt: str = ""
    popup: ScenePopup = ScenePopup()
    motion: SceneMotion = SceneMotion()
    transition_out: SceneTransition = SceneTransition(type="fade")
    on_screen_text: str | None = None
    image: SceneImage = SceneImage()
    locked: SceneLocks = SceneLocks()
    notes: list[str] = []

    @field_validator("sentence_ids")
    @classmethod
    def _at_least_one_sentence(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a scene must cover at least one sentence")
        return value

    @property
    def has_text(self) -> bool:
        return bool((self.popup.text or "").strip() or (self.on_screen_text or "").strip())


class StoryboardVariety(BaseModel):
    scenes: int = 0
    avg_scene_s: float = 0.0
    popup_share: float = 0.0
    distinct_transitions: int = 0
    distinct_motions: int = 0
    warnings: list[str] = []


class StoryboardDoc(BaseModel):
    """``04_storyboard/storyboard.json``."""

    project_id: str
    format: Literal["long", "shorts"]
    aspect: Aspect
    style_guide: str = ""
    negative_rules: str = ""
    popup_style: str = ""
    generated_at: datetime
    model: str
    speaking_rate_wpm: int = 150
    scenes: list[Scene]
    variety: StoryboardVariety = StoryboardVariety()
    gate_results: list[dict[str, Any]] = []
    notes: list[str] = []
    schema_version: int = 1

    @property
    def total_s(self) -> float:
        return round(self.scenes[-1].est_end_s, 2) if self.scenes else 0.0


# What Claude returns -------------------------------------------------------------------------


class StoryboardDraftScene(BaseModel):
    sentence_ids: list[str]
    image_prompt: str = Field(description="one text-free picture, no real people or logos")
    negative_prompt: str = ""
    popup_text: str | None = Field(default=None, description="at most 6 words, or null")
    popup_position: str = "bottom-left"
    motion_preset: str = "zoom_in"
    transition_type: str = "fade"
    on_screen_text: str | None = None


class StoryboardDraft(BaseModel):
    scenes: list[StoryboardDraftScene]


# Edits -------------------------------------------------------------------------------------


class StoryboardApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/storyboard/approve``: the whole edited document."""

    storyboard: StoryboardDoc


class StoryboardReviewPayload(BaseModel):
    stage: Literal["storyboard"] = "storyboard"
    storyboard: StoryboardDoc
    transitions: list[dict[str, Any]] = Field(
        default=[], description="the allowed transition types with labels, for the pickers"
    )
    motion_presets: list[str] = list(MOTION_PRESETS)
    popup_positions: list[str] = list(POPUP_POSITIONS)
    scene_band_s: list[float] = [8.0, 12.0]
