"""Channel configuration: the data behind the in-app Channel Setup form.

One channel = one JSON file at <shared_dir>/channels/<slug>/channel.json.
This module is the single source of truth for the shape; the front end mirrors it in
frontend/src/types/channel.ts. Never store API keys here: providers reference the name of
an environment variable instead.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

Language = Literal[
    "English", "Spanish", "Hindi", "Arabic", "Portuguese", "Indonesian",
    "Japanese", "German", "French", "Russian", "Vietnamese", "Turkish",
    "Korean", "Urdu", "Italian", "Other",
]

VideoFormat = Literal["long", "shorts", "both"]
ChannelStatus = Literal["setup", "active", "paused", "archived"]
StageMode = Literal["auto", "review", "manual"]
Priority = Literal[1, 2, 3]  # 1 main competitor, 2 secondary, 3 watch only

FrameworkType = Literal[
    "title", "script_long", "script_shorts", "scene_prompt", "thumbnail", "seo", "style_guide",
    "other",
]
VoiceTool = Literal[
    "ai33", "minimax", "cartesia", "inworld", "fish_audio", "azure", "google", "local",
    "other",
]
ImageTool = Literal[
    "google_gemini", "openai", "flux_bfl", "flux_fal", "flux_replicate", "ideogram",
    "recraft", "leonardo", "local", "manual", "other",
]
ThumbnailFace = Literal["none", "face", "ai_character"]


class StageName(StrEnum):
    research = "research"
    title = "title"
    script = "script"
    storyboard = "storyboard"
    voice = "voice"
    images = "images"
    edit = "edit"
    export = "export"


class ChannelIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl | None = None
    id: str | None = Field(default=None, description="YouTube channel id, filled by the app")
    language: Language = "English"
    secondary_languages: list[Language] = []
    niche: str = ""
    audience: str = ""
    formats: VideoFormat = "both"
    long_form_minutes: int = Field(default=10, ge=1, le=60)
    shorts_seconds: int = Field(default=45, ge=10, le=180)
    videos_per_week: int = Field(default=5, ge=0, le=100)
    export_folder: str = ""
    music_folder: str = ""
    brand_colors: list[str] = []
    caption_style: str = ""
    owner: str = ""
    browser_profile: int | None = None
    status: ChannelStatus = "setup"

    @field_validator("brand_colors")
    @classmethod
    def _hex_colors(cls, value: list[str]) -> list[str]:
        for color in value:
            if not (color.startswith("#") and len(color) in (4, 7)):
                raise ValueError(f"brand colour must be a hex value like #1F3864, got {color!r}")
        return [c.upper() for c in value]


class Competitor(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    id: str | None = Field(default=None, description="filled by the app")
    language: Language = "English"
    priority: Priority = 1
    why: str = ""
    videos_found: int | None = Field(default=None, description="filled by the app")
    last_scanned: datetime | None = Field(default=None, description="filled by the app")


class Framework(BaseModel):
    type: FrameworkType
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(default="", description="file path (shared folder) or link")
    version: str = ""
    formats: VideoFormat = "both"
    notes: str = ""


class VoiceConsent(BaseModel):
    """The consent record for a cloned voice (only the team's own enrolled voices may be
    cloned). Filled in the Channel Setup form; empty for a stock voice. A ``consent.json``
    next to the voice sample is the alternative place for the same record."""

    owner_name: str = Field(default="", description="whose voice this is")
    consented_by: str = Field(default="", description="who recorded the consent")
    consented_at: datetime | None = None
    statement: str = Field(default="", description="the text the owner agreed to")

    @property
    def is_filled(self) -> bool:
        return bool(self.owner_name.strip())


class VoiceConfig(BaseModel):
    tool: VoiceTool = "other"
    clone_ref: str = Field(default="", description="clone link or voice id")
    name: str = ""
    language: Language = "English"
    model: str = ""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    style: str = ""
    sample_path: str = ""
    returns_word_timestamps: bool | None = None
    api_key_env: str = Field(default="", pattern=r"^[A-Z0-9_]*$")
    monthly_budget_characters: int | None = None
    consent: VoiceConsent = VoiceConsent()


class ImageConfig(BaseModel):
    tool: ImageTool = "google_gemini"
    model: str = ""
    style_guide: str = ""
    negative_rules: str = ""
    aspect_long: str = "16:9"
    aspect_shorts: str = "9:16"
    resolution: str = "1920x1080"
    reference_folder: str = ""
    on_image_text: Literal["app_popups", "none"] = "app_popups"
    popup_style: str = ""
    api_key_env: str = Field(default="", pattern=r"^[A-Z0-9_]*$")
    monthly_budget_images: int | None = None


class ThumbnailConfig(BaseModel):
    template_videos: list[HttpUrl] = []
    headline_font: str = ""
    headline_colors: str = ""
    max_headline_words: int = Field(default=4, ge=1, le=12)
    face: ThumbnailFace = "none"
    must_include: str = ""
    must_avoid: str = ""
    sizes: list[str] = ["1280x720", "1080x1920"]


class StageModes(BaseModel):
    research: StageMode = "review"
    title: StageMode = "review"
    script: StageMode = "review"
    storyboard: StageMode = "review"
    voice: StageMode = "auto"
    images: StageMode = "review"
    edit: StageMode = "review"
    export: StageMode = "review"


class Channel(BaseModel):
    """Everything needed to start work on one channel."""

    slug: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", description="folder name, derived from the name"
    )
    channel: ChannelIdentity
    competitors: list[Competitor] = []
    frameworks: list[Framework] = []
    voice: VoiceConfig = VoiceConfig()
    images: ImageConfig = ImageConfig()
    thumbnail: ThumbnailConfig = ThumbnailConfig()
    stage_modes: StageModes = StageModes()
    reviewer: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    schema_version: int = 1


class ChannelSummary(BaseModel):
    """Row for the channel list."""

    slug: str
    name: str
    language: Language
    formats: VideoFormat
    status: ChannelStatus
    competitors: int
    updated_at: datetime | None
