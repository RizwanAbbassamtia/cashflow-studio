"""The ``research.scan`` background job behind ``POST /api/channels/{slug}/research/scan``.

The job record lives in the shared registry (``pipeline/jobs.py``), which ``GET /api/jobs/{id}``
reads. One scan per channel runs at a time: starting another while one is active returns the
running job.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..models.channel import Channel
from ..models.research import ScanJobResult, ScanRequest
from ..pipeline.jobs import Job, JobRegistry, ProgressFn
from ..pipeline.jobs import registry as default_registry
from ..storage.channel_store import ChannelStore
from .cache import ResearchCache
from .config import ResearchConfig, cached_research_config
from .errors import ResearchError
from .provider import ResearchProvider, build_provider
from .scanner import (
    candidates_for,
    history_for,
    scan_competitors,
    write_back_competitor_stats,
)

JOB_KIND = "research.scan"


def utc_now() -> datetime:
    return datetime.now(UTC)


def run_scan(
    channel: Channel,
    request: ScanRequest,
    *,
    provider: ResearchProvider,
    cache: ResearchCache,
    config: ResearchConfig,
    progress: ProgressFn | None = None,
    store: ChannelStore | None = None,
    now: datetime | None = None,
) -> ScanJobResult:
    """Scan all competitors, rank both formats and (optionally) update the channel file."""
    now = now or utc_now()
    outcome = scan_competitors(
        channel,
        provider,
        cache,
        config=config,
        tabs=request.tabs or config.tabs,
        max_videos=request.max_videos_per_channel or config.max_videos_per_channel,
        force=request.force,
        now=now,
        progress=progress,
    )
    history = history_for(cache, channel.slug)
    long_candidates = candidates_for(outcome, "long", now=now, config=config, history=history)
    shorts_candidates = candidates_for(outcome, "shorts", now=now, config=config, history=history)
    if store is not None:
        if progress:
            progress("Saving the results on the channel", 95.0)
        write_back_competitor_stats(store, channel.slug, outcome.statuses)
    return ScanJobResult(
        channel_slug=channel.slug,
        scanned_at=outcome.scanned_at or now,
        channels=outcome.statuses,
        videos_found=outcome.videos_found,
        candidates_long=len(long_candidates),
        candidates_shorts=len(shorts_candidates),
    )


def start_scan_job(
    *,
    settings: Any,
    channel: Channel,
    request: ScanRequest | None = None,
    store: ChannelStore | None = None,
    provider: ResearchProvider | None = None,
    cache: ResearchCache | None = None,
    config: ResearchConfig | None = None,
    registry: JobRegistry | None = None,
    provider_factory: Callable[[], ResearchProvider] | None = None,
) -> Job:
    """Queue a scan in a background thread and return its job (202 ``{job_id}``)."""
    registry = registry or default_registry
    active = registry.find(JOB_KIND, active_only=True, channel_slug=channel.slug)
    if active:
        return active[0]
    request = request or ScanRequest()
    config = config or cached_research_config()
    cache = cache or ResearchCache(settings.app_data_dir)
    count = len(channel.competitors)
    job = registry.create(
        JOB_KIND,
        message=f"Scanning {count} competitor channel{'s' if count != 1 else ''}",
        meta={"channel_slug": channel.slug},
    )

    def work(_job: Job, progress: ProgressFn) -> dict[str, Any]:
        def on_wait(seconds: float, reason: str) -> None:
            minutes = max(1, round(seconds / 60))
            progress(
                f"YouTube asked us to slow down ({reason}). Waiting {minutes} minute"
                f"{'s' if minutes != 1 else ''} before trying again.",
                None,
            )

        active_provider = provider or (provider_factory() if provider_factory else None)
        if active_provider is None:
            active_provider = build_provider(settings, config=config, on_wait=on_wait)
        result = run_scan(
            channel,
            request,
            provider=active_provider,
            cache=cache,
            config=config,
            progress=progress,
            store=store,
        )
        return result.model_dump(mode="json")

    registry.run_in_thread(job.id, work, friendly_errors=(ResearchError,))
    return job
