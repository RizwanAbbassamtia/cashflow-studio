"""Export stage data: the thumbnail template Claude reads off a competitor thumbnail, the
three thumbnail variants, the SEO pack (``08_export/metadata.json``), the synthetic-media
disclosure, the provenance bundle and the approval edits. See docs/M3-M4-CONTRACT.md
section 4.

Boxes are fractions of the frame ``[x, y, w, h]`` in 0-1 so one template serves both the
16:9 and the 9:16 thumbnail. The front end mirrors these in ``frontend/src/types/export.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ThumbnailVariantId = Literal["v1", "v2", "v3"]
VARIANT_IDS: tuple[ThumbnailVariantId, ...] = ("v1", "v2", "v3")
TemplateSource = Literal["llm", "cache", "neutral"]
ExportFileKind = Literal["video", "thumbnail", "metadata", "provenance"]

DEFAULT_SUBJECT_BOX = [0.30, 0.05, 0.68, 0.90]
DEFAULT_HEADLINE_BOX = [0.04, 0.60, 0.92, 0.34]

# YouTube limits (support.google.com/youtube/answer/12340300 and /answer/9884579).
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
MAX_TAGS_CHARS = 500
MAX_HASHTAGS = 15
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0


def _clean_box(value: Any, fallback: list[float]) -> list[float]:
    """Four numbers in 0-1. Pixel values (anything above 1.5) are read as 1280x720 pixels."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return list(fallback)
    try:
        numbers = [float(v) for v in value]
    except (TypeError, ValueError):
        return list(fallback)
    if max(numbers) > 1.5:
        numbers = [numbers[0] / 1280, numbers[1] / 720, numbers[2] / 1280, numbers[3] / 720]
    x, y, w, h = (min(1.0, max(0.0, n)) for n in numbers)
    if w <= 0.02 or h <= 0.02:
        return list(fallback)
    return [round(x, 4), round(y, 4), round(min(w, 1.0 - x), 4), round(min(h, 1.0 - y), 4)]


# What Claude reads off a competitor thumbnail (kept simple so the schema stays stable) --------


class ThumbnailTextBlock(BaseModel):
    box: list[float] = Field(
        default_factory=lambda: list(DEFAULT_HEADLINE_BOX),
        description="[x, y, w, h] as fractions of the frame",
    )
    role: str = Field(default="headline", description="headline, subline, badge or label")
    color: str = Field(default="#FFFFFF", description="text colour as a hex value")
    stroke: str = Field(default="#000000", description="outline colour as a hex value, or ''")

    @field_validator("box", mode="before")
    @classmethod
    def _box(cls, value: Any) -> list[float]:
        return _clean_box(value, DEFAULT_HEADLINE_BOX)


class ThumbnailTemplate(BaseModel):
    """Layout read from a competitor thumbnail: where the subject sits, where the text goes,
    which colours and mood it uses. The app never copies the picture, only the layout."""

    subject_box: list[float] = Field(
        default_factory=lambda: list(DEFAULT_SUBJECT_BOX),
        description="[x, y, w, h] where the main subject sits",
    )
    text_blocks: list[ThumbnailTextBlock] = []
    palette: list[str] = Field(default=[], description="up to 5 dominant colours as hex")
    mood: str = Field(default="", description="a few words: tense, warm, mysterious ...")
    has_face: bool = False
    layout_notes: str = Field(default="", description="one or two sentences on the layout")

    @field_validator("subject_box", mode="before")
    @classmethod
    def _subject(cls, value: Any) -> list[float]:
        return _clean_box(value, DEFAULT_SUBJECT_BOX)

    @field_validator("palette", mode="before")
    @classmethod
    def _palette(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:8]

    def headline_block(self) -> ThumbnailTextBlock:
        for block in self.text_blocks:
            if "head" in block.role.lower() or "title" in block.role.lower():
                return block
        return self.text_blocks[0] if self.text_blocks else ThumbnailTextBlock()


class ThumbnailTemplateFile(BaseModel):
    """``<channel folder>/thumbnail-templates/<video_id>.json``."""

    video_id: str
    template: ThumbnailTemplate
    source: TemplateSource = "llm"
    model: str = ""
    cached_at: datetime
    image_sha256: str = ""
    schema_version: int = 1


class ThumbnailVariant(BaseModel):
    id: ThumbnailVariantId
    headline: str
    file: str = Field(description="file name inside 08_export (16:9, 1280x720)")
    file_shorts: str = Field(description="file name inside 08_export (9:16, 1080x1920)")
    distance: int | None = Field(
        default=None, description="pHash distance to the competitor thumbnail (16:9)"
    )


# SEO pack ----------------------------------------------------------------------------------


class Chapter(BaseModel):
    time: str = Field(description="mm:ss or h:mm:ss")
    title: str


class SeoPack(BaseModel):
    """The YouTube metadata in the target language."""

    title: str
    description: str = ""
    tags: list[str] = Field(default=[], description="at most 500 characters in total")
    chapters: list[Chapter] = []
    pinned_comment: str = ""
    hashtags: list[str] = []


class SeoLLMOutput(BaseModel):
    """What Claude returns for the ``seo`` task (every field optional so a partial answer
    still validates; the stage fills the gaps from the script)."""

    title: str = ""
    description: str = ""
    tags: list[str] = []
    chapters: list[Chapter] = []
    pinned_comment: str = ""
    hashtags: list[str] = []
    headlines: list[str] = Field(
        default=[], description="three different thumbnail headlines, each a few words"
    )
    title_promise_early: bool | None = Field(
        default=None, description="the title's claim appears in the first fifth of the script"
    )
    title_promise_note: str = ""


# Disclosure and provenance --------------------------------------------------------------------


class ImageProvenance(BaseModel):
    scene: int
    provider: str = ""
    model: str = ""
    synthid: bool | None = None
    c2pa: bool | None = None
    path: str = ""


class VoiceProvenance(BaseModel):
    provider: str = ""
    model: str = ""
    consent_ref: str = ""


class ProvenanceSummary(BaseModel):
    images: list[ImageProvenance] = []
    voice: VoiceProvenance = VoiceProvenance()


class Disclosure(BaseModel):
    altered_or_synthetic: bool = Field(
        default=True, description="tick YouTube's altered or synthetic content box"
    )
    provenance: ProvenanceSummary = ProvenanceSummary()


class ExportMetadata(BaseModel):
    """``08_export/metadata.json``: everything the uploader needs."""

    project_id: str
    channel_slug: str
    format: Literal["long", "shorts"]
    language: str
    title: str
    description: str = ""
    tags: list[str] = []
    chapters: list[Chapter] = []
    pinned_comment: str = ""
    hashtags: list[str] = []
    thumbnail: str = Field(default="", description="chosen 16:9 thumbnail file name")
    thumbnail_shorts: str = Field(default="", description="chosen 9:16 thumbnail file name")
    videos: dict[str, str] = Field(default={}, description="preset -> exported file name")
    disclosure: Disclosure = Disclosure()
    generated_at: datetime
    model: str = ""
    notes: list[str] = []
    schema_version: int = 1

    def seo(self) -> SeoPack:
        return SeoPack(
            title=self.title,
            description=self.description,
            tags=list(self.tags),
            chapters=list(self.chapters),
            pinned_comment=self.pinned_comment,
            hashtags=list(self.hashtags),
        )


class LicenceRecord(BaseModel):
    item: str
    licence: str = ""
    path: str = ""
    ok: bool | None = None


class ProvenanceDoc(BaseModel):
    """``08_export/provenance.json`` (``provenance.md`` is rendered from it)."""

    project_id: str
    channel_slug: str
    title: str
    language: str
    format: str
    generated_at: datetime
    research: dict[str, Any] = {}
    title_variants: list[dict[str, Any]] = []
    script: dict[str, Any] = {}
    prompts: dict[str, Any] = Field(
        default={}, description="prompt files used per stage and the per-scene image prompts"
    )
    qa: list[dict[str, Any]] = Field(default=[], description="image QA verdicts per scene")
    images: list[ImageProvenance] = []
    voice: VoiceProvenance = VoiceProvenance()
    render: dict[str, Any] = {}
    thumbnail: dict[str, Any] = {}
    disclosure: Disclosure = Disclosure()
    review_log: dict[str, list[str]] = Field(default={}, description="job.json history per stage")
    licences: list[LicenceRecord] = []
    costs: dict[str, float] = {}
    schema_version: int = 1


# The export record and the review payload -----------------------------------------------------


class ExportedFile(BaseModel):
    name: str
    source: str = Field(description="path inside the project folder")
    bytes: int = 0
    kind: ExportFileKind = "video"


class ExportDoc(BaseModel):
    """``08_export/export.json``: what the stage did, so edits and a restart can build on it."""

    project_id: str
    exported_at: datetime = Field(
        description="when the pack was written; after the export, when it was copied"
    )
    export_folder: str
    topic_slug: str
    presets: list[str] = []
    missing_presets: list[str] = []
    exported: bool = Field(
        default=False,
        description="the files were copied into export_folder (on approval, or on the run "
        "when the stage is set to auto)",
    )
    files: list[ExportedFile] = Field(
        default=[], description="what is in the export folder; empty until exported"
    )
    thumbnails: list[ThumbnailVariant] = []
    thumbnail_choice: ThumbnailVariantId = "v1"
    template: ThumbnailTemplate = ThumbnailTemplate()
    template_source: TemplateSource = "neutral"
    competitor_video_id: str | None = None
    competitor_thumbnail: str | None = Field(
        default=None, description="path of the competitor thumbnail used for the gate"
    )
    subject_provider: str = ""
    subject_model: str = ""
    subject_files: dict[str, str] = Field(
        default={}, description="aspect -> text-free subject image inside 08_export"
    )
    title_promise_early: bool | None = None
    title_promise_note: str = ""
    gate_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    costs: dict[str, float] = {}
    model: str = ""
    schema_version: int = 1

    def variant(self, variant_id: str) -> ThumbnailVariant | None:
        for variant in self.thumbnails:
            if variant.id == variant_id:
                return variant
        return None

    def chosen(self) -> ThumbnailVariant | None:
        return self.variant(self.thumbnail_choice) or (
            self.thumbnails[0] if self.thumbnails else None
        )


class ExportApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/export/approve`` (and on a redo)."""

    metadata: dict[str, Any] | None = None
    thumbnail_choice: ThumbnailVariantId | None = None
    altered_or_synthetic: bool | None = None
    headline: str | None = Field(default=None, max_length=120)


class ThumbnailView(BaseModel):
    id: ThumbnailVariantId
    headline: str
    url: str
    url_shorts: str
    file: str
    file_shorts: str
    distance: int | None = None


class ExportReviewPayload(BaseModel):
    stage: Literal["export"] = "export"
    thumbnails: list[ThumbnailView] = []
    thumbnail_choice: ThumbnailVariantId = "v1"
    max_headline_words: int = 4
    metadata: ExportMetadata
    disclosure: Disclosure = Disclosure()
    export_folder: str = ""
    exported: bool = Field(
        default=False, description="the files are in the export folder (Approve = export)"
    )
    files: list[ExportedFile] = []
    presets: list[str] = []
    missing_presets: list[str] = []
    provenance_url: str = ""
    provenance_md_url: str = ""
    template_source: TemplateSource = "neutral"
    title_promise_early: bool | None = None
    title_promise_note: str = ""
    warnings: list[str] = []
    gate_results: list[dict[str, Any]] = []
    costs: dict[str, float] = {}
