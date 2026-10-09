"""Scanning a channel's competitors and turning the results into ranked candidates.

``scan_competitors`` lists every competitor's tabs through a :class:`ResearchProvider`,
using the SQLite cache when a tab was listed less than ``cache_minutes`` ago (unless forced),
stores fresh listings, and records per-competitor errors instead of failing the whole scan.
Only :class:`ResearchBlocked` (YouTube asked us to stop) aborts a scan.

``cached_outcome`` answers the candidates endpoint from the cache alone, and
``candidates_for`` runs the outlier maths across all competitors for one format.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..models.channel import Channel, Competitor
from ..models.research import (
    Candidate,
    ResearchFormat,
    ScanChannelStatus,
    Tab,
    TabListing,
)
from ..storage import channel_store
from ..storage.channel_store import (
    ChannelNotFound,
    ChannelStore,
    ChannelStoreError,
    validate_url,
)
from .cache import ResearchCache
from .config import ResearchConfig
from .errors import ResearchBlocked, ResearchError
from .outliers import UsedHistory, build_candidates
from .picker import normalise_channel_url
from .provider import ResearchProvider

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float | None], None]
NOT_SCANNED = "Not scanned yet"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ScanOutcome:
    channel_slug: str
    listings: list[TabListing] = field(default_factory=list)
    statuses: list[ScanChannelStatus] = field(default_factory=list)

    @property
    def scanned_at(self) -> datetime | None:
        stamps = [listing.scanned_at for listing in self.listings]
        return max(stamps) if stamps else None

    @property
    def videos_found(self) -> int:
        return sum(len(listing.videos) for listing in self.listings)


def competitor_url(competitor: Competitor) -> str:
    """The canonical channel link used as the cache key (``validate_url`` form)."""
    raw = str(competitor.url)
    check = validate_url(raw)
    if check.ok and check.kind == "channel":
        return check.normalized
    return raw.rstrip("/")


def default_format(channel: Channel) -> ResearchFormat:
    return "shorts" if channel.channel.formats == "shorts" else "long"


def scan_competitors(
    channel: Channel,
    provider: ResearchProvider,
    cache: ResearchCache,
    *,
    config: ResearchConfig,
    tabs: list[Tab] | None = None,
    max_videos: int | None = None,
    force: bool = False,
    now: datetime | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> ScanOutcome:
    """List every competitor's tabs (fresh cache first) and store what was found.

    ``cancel`` (set when the project is archived) stops the listing between tabs; what was
    listed so far is returned and the caller decides what to do with it.
    """
    tabs = list(tabs or config.tabs)
    max_videos = max_videos or config.max_videos_per_channel
    now = now or utc_now()
    outcome = ScanOutcome(channel_slug=channel.slug)
    steps = max(len(channel.competitors) * len(tabs), 1)
    done = 0
    for competitor in channel.competitors:
        if cancel is not None and cancel.is_set():
            break
        url = competitor_url(competitor)
        status = ScanChannelStatus(name=competitor.name, url=url, id=competitor.id)
        errors: list[str] = []
        for tab in tabs:
            if cancel is not None and cancel.is_set():
                break
            if progress:
                progress(f"Reading {competitor.name} ({tab})", 90.0 * done / steps)
            listing = _fresh_listing(cache, url, tab, config, now) if not force else None
            if listing is None:
                try:
                    listing = provider.list_tab(url, tab, max_videos=max_videos)
                except ResearchBlocked:
                    raise
                except ResearchError as exc:
                    log.info("Listing %s/%s failed: %s", url, tab, exc)
                    cache.save_scan_error(url, tab, str(exc), now)
                    errors.append(str(exc))
                    listing = cache.load_listing(url, tab)  # keep the last good listing
                else:
                    cache.save_listing(listing)
            if listing is not None:
                _apply_listing(status, listing)
                outcome.listings.append(listing)
            done += 1
        if errors:
            status.error = errors[0]
        outcome.statuses.append(status)
    if progress:
        progress("Ranking the videos", 90.0)
    return outcome


def cached_outcome(
    channel: Channel, cache: ResearchCache, *, tabs: list[Tab] | None = None
) -> ScanOutcome:
    """What the cache holds for a channel's competitors, without touching YouTube."""
    tabs = list(tabs or ["videos", "shorts"])
    outcome = ScanOutcome(channel_slug=channel.slug)
    for competitor in channel.competitors:
        url = competitor_url(competitor)
        status = ScanChannelStatus(name=competitor.name, url=url, id=competitor.id)
        seen_any = False
        for tab in tabs:
            scan = cache.get_scan(url, tab)
            if scan is None:
                continue
            seen_any = True
            if scan.error and not status.error:
                status.error = scan.error
            listing = cache.load_listing(url, tab)
            if listing is not None:
                _apply_listing(status, listing)
                outcome.listings.append(listing)
        if not seen_any:
            status.error = NOT_SCANNED
        outcome.statuses.append(status)
    return outcome


def history_for(
    cache: ResearchCache, channel_slug: str, *, exclude_project_id: str | None = None
) -> UsedHistory:
    """Picked competitor videos (``research_picks``) plus the channel's produced titles."""
    picked = cache.used_video_ids(channel_slug, exclude_project_id=exclude_project_id)
    return UsedHistory(
        video_ids=frozenset(picked),
        titles=frozenset(cache.used_titles(channel_slug)),
    )


def candidates_for(
    outcome: ScanOutcome,
    fmt: ResearchFormat,
    *,
    now: datetime | None = None,
    config: ResearchConfig | None = None,
    history: UsedHistory | None = None,
) -> list[Candidate]:
    return build_candidates(
        outcome.listings, fmt, now=now or utc_now(), config=config, history=history
    )


def write_back_competitor_stats(
    store: ChannelStore, slug: str, statuses: list[ScanChannelStatus]
) -> bool:
    """Fill ``id``, ``videos_found`` and ``last_scanned`` on the channel's competitors.

    Best effort: the channel file is re-read under the store lock so edits made meanwhile
    are kept; any problem is logged and ``False`` returned.
    """
    by_url = {normalise_channel_url(s.url): s for s in statuses}
    try:
        with channel_store.LOCK:
            channel = store.get(slug)
            changed = False
            for competitor in channel.competitors:
                status = by_url.get(normalise_channel_url(str(competitor.url)))
                if status is None or status.last_scanned is None:
                    continue
                competitor.id = competitor.id or status.id
                competitor.videos_found = status.videos_found
                competitor.last_scanned = status.last_scanned
                changed = True
            if changed:
                store.update(slug, channel)
            return changed
    except ChannelNotFound:
        log.info("Channel %s has no file to save scan results into", slug)
        return False
    except (ChannelStoreError, OSError) as exc:
        log.warning("Could not save scan results on channel %s: %s", slug, exc)
        return False


def _fresh_listing(
    cache: ResearchCache, url: str, tab: Tab, config: ResearchConfig, now: datetime
) -> TabListing | None:
    scan = cache.get_scan(url, tab)
    if scan is None or not scan.is_fresh(config.cache_minutes, now):
        return None
    return cache.load_listing(url, tab)


def _apply_listing(status: ScanChannelStatus, listing: TabListing) -> None:
    status.id = status.id or listing.channel_id
    status.videos_found += len(listing.videos)
    if status.last_scanned is None or listing.scanned_at > status.last_scanned:
        status.last_scanned = listing.scanned_at
