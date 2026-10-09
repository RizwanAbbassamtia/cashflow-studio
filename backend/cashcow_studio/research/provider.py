"""The research provider interface and the factory that picks an implementation.

Two implementations exist: :class:`~cashcow_studio.research.ytdlp_client.YtDlpProvider`
(real YouTube data through yt-dlp) and :class:`~cashcow_studio.research.mock.MockProvider`
(deterministic fixtures, no network). ``CCS_RESEARCH_PROVIDER=mock`` selects the mock.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models.research import Tab, TabListing, TranscriptDoc, VideoDetails
from .config import ResearchConfig, cached_research_config

PROVIDER_ENV = "CCS_RESEARCH_PROVIDER"
PROVIDER_NAMES: tuple[str, ...] = ("ytdlp", "mock")


@runtime_checkable
class ResearchProvider(Protocol):
    """What the scanner and the research stage need from YouTube."""

    name: str

    def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
        """Flat entries of a channel's ``/videos`` or ``/shorts`` tab, newest first."""
        ...

    def video_details(self, video_id: str) -> VideoDetails:
        """Exact numbers, tags, description, chapters and caption languages of one video."""
        ...

    def transcript(self, video_id: str, lang: str) -> TranscriptDoc:
        """Captions with word timings; raises ``TranscriptUnavailable`` when there are none."""
        ...

    def thumbnail(self, video_id: str, dest: Path) -> Path:
        """Save the best available thumbnail as JPEG at ``dest`` and return it."""
        ...


def provider_name(settings: Any = None) -> str:
    """``CCS_RESEARCH_PROVIDER`` wins; then ``settings.research_provider`` if the settings
    model has it; default ``ytdlp``."""
    from_env = os.environ.get(PROVIDER_ENV, "").strip().lower()
    if from_env:
        return from_env
    from_settings = getattr(settings, "research_provider", None)
    if isinstance(from_settings, str) and from_settings.strip():
        return from_settings.strip().lower()
    return "ytdlp"


def build_provider(
    settings: Any = None,
    *,
    name: str | None = None,
    config: ResearchConfig | None = None,
    on_wait: Callable[[float, str], None] | None = None,
) -> ResearchProvider:
    """A provider for the current settings; unknown names fall back to yt-dlp.

    ``on_wait(seconds, reason)`` is told before every back-off sleep, so a scan job or a
    stage can show "YouTube asked us to slow down; waiting 10 minutes" instead of silence.
    """
    name = (name or provider_name(settings)).lower()
    config = config or cached_research_config()
    if name == "mock":
        from .mock import MockProvider

        return MockProvider()
    from .ytdlp_client import YtDlpProvider

    app_data_dir = getattr(settings, "app_data_dir", None)
    cache_dir = Path(app_data_dir) / "cache" / "yt-dlp" if app_data_dir else None
    return YtDlpProvider(config=config, cache_dir=cache_dir, on_wait=on_wait)
