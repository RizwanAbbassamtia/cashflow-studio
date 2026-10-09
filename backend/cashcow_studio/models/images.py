"""Images stage data: ``06_images/images.json`` (:class:`ImagesDoc`), the vision QA verdict
(:class:`ImageQA`), the approval edits and the review payload.
See docs/M3-M4-CONTRACT.md section 2.

Scenes are addressed by the storyboard scene ``index`` (0-based, ``Scene.index``); the file
of scene ``index`` is ``06_images/scene_NN.png`` with ``NN = index + 1`` (two digits at
least). The front end mirrors these in ``frontend/src/types/images.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..providers.image.base import Provenance

ImageSource = Literal["provider", "upload"]
ImageRecordStatus = Literal["pending", "generated", "approved", "rejected"]
StyleSheetSource = Literal["none", "existing", "channel_reference", "first_scene"]

MIN_QA_SCORE = 5
MAX_QA_RETRIES = 3


def scene_file_name(index: int) -> str:
    """``scene_01.png`` for storyboard scene index 0."""
    return f"scene_{index + 1:02d}.png"


class ImageQA(BaseModel):
    """What the vision model says about one generated picture."""

    matches_prompt: bool = Field(description="the picture shows what the scene prompt asked")
    has_text: bool = Field(description="readable words, letters, captions or numbers")
    has_real_person: bool = Field(description="a recognisable real person or a public figure")
    has_logo: bool = Field(description="a brand mark, logo or trademark")
    artifacts: list[str] = Field(
        default=[], description="visible flaws: extra fingers, warped faces, broken geometry"
    )
    score: int = Field(ge=0, le=10, description="overall quality, 0 (unusable) to 10 (perfect)")
    reason: str = Field(default="", description="one plain sentence a reviewer can read")

    def rejection_reasons(self, min_score: int = MIN_QA_SCORE) -> list[str]:
        """Why the picture is rejected; empty when it passes."""
        reasons: list[str] = []
        if not self.matches_prompt:
            reasons.append("it does not show what the scene asked for")
        if self.has_text:
            reasons.append("it contains text")
        if self.has_real_person:
            reasons.append("it shows a real person")
        if self.has_logo:
            reasons.append("it contains a logo")
        if self.score < min_score:
            reasons.append(
                f"the quality score is {self.score} out of 10 (at least {min_score} needed)"
            )
        return reasons

    def accepted(self, min_score: int = MIN_QA_SCORE) -> bool:
        return not self.rejection_reasons(min_score)


class ImageAttempt(BaseModel):
    """One generation try for a scene, accepted or not."""

    attempt: int = Field(ge=1)
    file: str | None = Field(
        default=None, description="path relative to the project folder; None when nothing was saved"
    )
    seed: int | None = None
    prompt: str = ""
    qa: ImageQA | None = None
    accepted: bool = False
    reason: str = Field(default="", description="why it was rejected (plain English)")
    phash: str | None = None
    cost_usd: float = 0.0
    created_at: datetime | None = None


class SceneImageRecord(BaseModel):
    """The state of one scene's picture."""

    scene: int = Field(ge=0, description="storyboard scene index")
    file: str | None = Field(default=None, description="scene_NN.png inside 06_images, if any")
    status: ImageRecordStatus = "pending"
    source: ImageSource = "provider"
    provider: str = ""
    model: str = ""
    seed: int | None = None
    width: int = 0
    height: int = 0
    prompt: str = Field(default="", description="the full text sent to the image tool")
    scene_prompt: str = Field(
        default="", description="the storyboard's picture description this record was made for"
    )
    negative_prompt: str = ""
    cost_usd: float = Field(default=0.0, description="all tries of this scene, accepted or not")
    provenance: Provenance = Provenance()
    phash: str | None = None
    qa: ImageQA | None = None
    attempts: int = 0
    history: list[ImageAttempt] = []
    locked: bool = False
    duplicate_of: str | None = Field(
        default=None, description="plain note when the accepted picture looks like another"
    )
    error: str | None = Field(default=None, description="why there is no accepted picture")
    notes: list[str] = []

    @property
    def has_image(self) -> bool:
        return self.file is not None and self.status in ("generated", "approved")


class ImagesBudget(BaseModel):
    monthly_limit: int | None = None
    used_this_month: int = 0
    """Pictures generated for this channel this month before this run (all tries)."""
    generated_now: int = 0
    warning: str | None = None


class ImagesDoc(BaseModel):
    """``06_images/images.json``."""

    project_id: str
    channel_slug: str = ""
    format: Literal["long", "shorts"] = "long"
    aspect: Literal["16:9", "9:16"] = "16:9"
    size: str = Field(default="2K", description="size label or WIDTHxHEIGHT sent to the tool")
    provider: str = ""
    model: str = ""
    style_guide: str = ""
    style_sheet: str | None = Field(default=None, description="style_sheet.png inside 06_images")
    style_sheet_source: StyleSheetSource = "none"
    generated_at: datetime
    scenes: list[SceneImageRecord] = []
    cost_usd: float = Field(default=0.0, description="image tool spend of the last run")
    qa_cost_usd: float = Field(default=0.0, description="vision check spend of the last run")
    budget: ImagesBudget = ImagesBudget()
    gate_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    notes: list[str] = []
    schema_version: int = 1

    def record_for(self, scene: int) -> SceneImageRecord | None:
        for record in self.scenes:
            if record.scene == scene:
                return record
        return None

    @property
    def accepted_count(self) -> int:
        return sum(1 for r in self.scenes if r.has_image)


# Edits ---------------------------------------------------------------------------------------


class RegenerateEdit(BaseModel):
    scene: int = Field(ge=0)
    note: str = ""


class UploadEdit(BaseModel):
    scene: int = Field(ge=0)
    path: str = Field(min_length=1, description="relative to the project folder, or absolute")


class ImagesApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/images/approve`` (also carried by a redo)."""

    regenerate: list[RegenerateEdit] = []
    uploads: list[UploadEdit] = []
    lock: list[int] = []

    @property
    def is_empty(self) -> bool:
        return not (self.regenerate or self.uploads or self.lock)


# Review payload --------------------------------------------------------------------------------


class ImagesReviewScene(BaseModel):
    scene: int
    number: int = Field(description="1-based, as shown to people")
    file: str | None = None
    image_url: str | None = Field(default=None, description="/api/projects/{id}/files/06_images/..")
    status: ImageRecordStatus = "pending"
    source: ImageSource = "provider"
    verdict: str = Field(default="", description="plain English: Passed, Rejected: ..., Uploaded")
    qa: ImageQA | None = None
    attempts: int = 0
    locked: bool = False
    narration: str = ""
    prompt: str = Field(default="", description="the scene description without the style guide")
    seed: int | None = None
    cost_usd: float = 0.0
    duplicate_of: str | None = None
    error: str | None = None
    rejected_urls: list[str] = []


class ImagesReviewPayload(BaseModel):
    stage: Literal["images"] = "images"
    cost_kind: Literal["images"] = "images"
    project_id: str
    aspect: str
    size: str
    provider: str
    model: str
    style_sheet_url: str | None = None
    style_sheet_source: StyleSheetSource = "none"
    scenes: list[ImagesReviewScene] = []
    accepted: int = 0
    total: int = 0
    cost_usd: float = 0.0
    qa_cost_usd: float = 0.0
    budget: ImagesBudget = ImagesBudget()
    warnings: list[str] = []
    gate_results: list[dict[str, Any]] = []
    generated_at: datetime | None = None
