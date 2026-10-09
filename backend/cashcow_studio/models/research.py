"""Research data: what a competitor scan finds, how each video scores, and what the research
stage writes into ``01_research/``. See docs/M1-M2-CONTRACT.md section 2.

The front end mirrors these models in ``frontend/src/types/research.ts``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Tab = Literal["videos", "shorts"]
ResearchFormat = Literal["long", "shorts"]
OutlierLabel = Literal["one-of-ten", "strong", "notable", "normal"]
TranscriptSource = Literal["yt-dlp", "youtube-transcript-api", "mock", "none"]
PickKind = Literal["ai_pick", "manual_pick", "own_topic"]

TABS: tuple[Tab, ...] = ("videos", "shorts")


def format_for_tab(tab: Tab) -> ResearchFormat:
    return "shorts" if tab == "shorts" else "long"


def tab_for_format(fmt: ResearchFormat) -> Tab:
    return "shorts" if fmt == "shorts" else "videos"


# What yt-dlp returns ---------------------------------------------------------------------


class FlatVideo(BaseModel):
    """One entry of a channel's ``/videos`` or ``/shorts`` tab (approximate numbers)."""

    video_id: str
    url: str
    title: str
    channel_url: str = Field(description="the competitor link as listed on the channel")
    channel_id: str | None = None
    channel_name: str = ""
    tab: Tab
    format: ResearchFormat
    position: int = Field(ge=0, description="0 = newest on the tab")
    duration_s: int | None = None
    view_count: int | None = Field(default=None, description="rounded, as shown on the tab")
    published_at: datetime | None = Field(default=None, description="approximate")
    thumbnail_url: str | None = None
    live_status: str | None = Field(
        default=None, description="yt-dlp live_status: is_live, was_live, is_upcoming ..."
    )
    channel_follower_count: int | None = None
    fetched_at: datetime


class TabListing(BaseModel):
    """Everything one ``list_tab`` call learned about a channel tab."""

    channel_url: str
    tab: Tab
    channel_id: str | None = None
    channel_name: str = ""
    channel_follower_count: int | None = None
    scanned_at: datetime
    videos: list[FlatVideo] = []
    from_cache: bool = False


class Chapter(BaseModel):
    title: str
    start_s: float
    end_s: float | None = None


class CaptionLanguages(BaseModel):
    manual: list[str] = []
    auto: list[str] = []


class VideoDetails(BaseModel):
    """Exact numbers for one video (``video_details``), written to ``video.json``."""

    video_id: str
    url: str
    title: str
    channel_id: str | None = None
    channel_name: str = ""
    channel_url: str | None = None
    channel_follower_count: int | None = None
    duration_s: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    upload_date: date | None = None
    published_at: datetime | None = None
    tags: list[str] = []
    categories: list[str] = []
    description: str = ""
    chapters: list[Chapter] = []
    caption_languages: CaptionLanguages = CaptionLanguages()
    thumbnail_url: str | None = None
    live_status: str | None = None
    language: str | None = None
    fetched_at: datetime


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptWord(BaseModel):
    start: float
    end: float
    text: str


class TranscriptDoc(BaseModel):
    """``transcript.json``: caption segments and word timings for the picked video."""

    video_id: str
    language: str | None = None
    source: TranscriptSource = "none"
    kind: Literal["manual", "auto"] | None = None
    segments: list[TranscriptSegment] = []
    words: list[TranscriptWord] = []
    text: str = ""
    error: str | None = Field(default=None, description="why no transcript is available")

    @property
    def available(self) -> bool:
        return bool(self.segments)


# Scoring -----------------------------------------------------------------------------------


class Candidate(BaseModel):
    """One competitor video, scored against its neighbours and ranked across competitors."""

    video_id: str
    url: str
    title: str
    channel_name: str
    channel_url: str
    channel_id: str | None = None
    format: ResearchFormat
    views: int
    views_exact: bool = False
    published_at: datetime | None = None
    age_days: int
    duration_s: int | None = None
    baseline_views: float
    outlier_score: float
    vpd: float = Field(description="views per day")
    vpd_ratio: float
    sub_ratio: float
    label: OutlierLabel
    thumbnail_url: str | None = None
    used_before: bool = False
    excluded_reason: str | None = None
    rank: int = 0


class ScanChannelStatus(BaseModel):
    """Per-competitor line in the scan result and the candidates response."""

    name: str
    url: str
    id: str | None = None
    videos_found: int = 0
    last_scanned: datetime | None = None
    error: str | None = None


# API -----------------------------------------------------------------------------------------


class ScanRequest(BaseModel):
    tabs: list[Tab] | None = None
    max_videos_per_channel: int | None = Field(default=None, ge=1, le=2000)
    force: bool = False


class ScanJobResult(BaseModel):
    """``result`` of a finished ``research.scan`` job."""

    channel_slug: str
    scanned_at: datetime
    channels: list[ScanChannelStatus]
    videos_found: int
    candidates_long: int
    candidates_shorts: int


class CandidatesResponse(BaseModel):
    scanned_at: datetime | None
    format: ResearchFormat
    channels: list[ScanChannelStatus]
    candidates: list[Candidate]
    pick: Candidate | None = Field(
        default=None,
        description="the video the AI would start from (same picker as the research stage)",
    )
    pick_video_id: str | None = None


# Stage output ------------------------------------------------------------------------------


class ResearchPick(BaseModel):
    """``pick.json``: the one video (or the typed topic) production starts from."""

    kind: PickKind
    video_id: str | None = None
    url: str | None = None
    title: str | None = None
    channel_name: str | None = None
    channel_url: str | None = None
    topic_text: str | None = None
    strategy: str = Field(default="", description="top_outlier_fresh, person, own_topic ...")
    reason: str = ""
    picked_at: datetime
    candidate: Candidate | None = None


class ResearchReviewPayload(BaseModel):
    """What the research review panel shows: the top candidates with the pick highlighted."""

    stage: Literal["research"] = "research"
    source_kind: PickKind
    topic_text: str | None = None
    format: ResearchFormat
    scanned_at: datetime | None = None
    channels: list[ScanChannelStatus] = []
    candidates: list[Candidate] = []
    pick: Candidate | None = None
    pick_video_id: str | None = None
    transcript_available: bool = False
    thumbnail_file: str | None = None
    notes: list[str] = []

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
