"""Outlier maths for competitor videos. Pure functions, no I/O, fully unit-tested.

How one video is judged (docs/M1-M2-CONTRACT.md section 2):

* Long-form and Shorts are scored separately (a video's ``format`` comes from the tab it was
  listed on).
* **baseline** = median view count of its neighbours by position on the same channel
  (``neighbours_before`` newer and ``neighbours_after`` older videos), ignoring neighbours
  younger than ``baseline_min_age_days`` because their views are still growing. If no
  neighbour qualifies, the channel median (same age rule) is used. A baseline is never
  below 1, so a score can always be computed.
* ``outlier_score = views / baseline``
* ``vpd = views / max(age_days, 1)`` (views per day); ``vpd_ratio = vpd / median(vpd of the
  same neighbour window)``; a video with an unknown age has ``vpd = 0``.
* ``sub_ratio = views / channel followers`` (0 when the follower count is unknown).
* Label: ``>= 10`` one-of-ten, ``>= 5`` strong, ``>= 3`` notable, else normal.
* Rank across **all** competitors of the channel by ``outlier_score``, ties broken by
  ``vpd_ratio``; eligible videos first, excluded ones after them with their reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..models.research import Candidate, FlatVideo, OutlierLabel, ResearchFormat, TabListing
from .config import ExclusionConfig, LabelThresholds, ResearchConfig

LIVE_STATUSES: frozenset[str] = frozenset({"is_live", "post_live", "was_live"})
PREMIERE_STATUSES: frozenset[str] = frozenset({"is_upcoming"})

SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class VideoInput:
    """The minimum the maths needs about one video of one channel."""

    video_id: str
    position: int
    views: int
    age_days: float | None
    duration_s: float | None = None
    followers: int | None = None
    live_status: str | None = None


@dataclass(frozen=True)
class VideoScore:
    video_id: str
    baseline_views: float
    outlier_score: float
    vpd: float
    vpd_ratio: float
    sub_ratio: float
    label: OutlierLabel


@dataclass(frozen=True)
class UsedHistory:
    """What this channel already made: picked competitor video ids and produced titles."""

    video_ids: frozenset[str] = frozenset()
    titles: frozenset[str] = frozenset()
    """Case-folded titles from ``title_history``."""

    def contains(self, video_id: str, title: str) -> bool:
        return video_id in self.video_ids or title.casefold().strip() in self.titles


# Basic statistics ---------------------------------------------------------------------------


def median(values: Sequence[float]) -> float | None:
    """Standard median (mean of the two middle values for an even count); ``None`` if empty."""
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def age_days_of(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    return max((now - published_at).total_seconds() / SECONDS_PER_DAY, 0.0)


def views_per_day(views: int, age_days: float | None) -> float:
    """``views / max(age_days, 1)``; 0 when the age is unknown."""
    if age_days is None:
        return 0.0
    return views / max(age_days, 1.0)


def is_settled(age_days: float | None, min_age_days: float) -> bool:
    """Old enough to count in a baseline. An unknown age is treated as settled."""
    return age_days is None or age_days >= min_age_days


# Windows and baselines -------------------------------------------------------------------


def neighbour_indexes(count: int, index: int, before: int, after: int) -> list[int]:
    """Positions ``before`` newer and ``after`` older than ``index`` (the video itself is out)."""
    start = max(0, index - before)
    stop = min(count, index + after + 1)
    return [i for i in range(start, stop) if i != index]


def settled_window(
    ordered: Sequence[VideoInput], index: int, before: int, after: int, min_age_days: float
) -> list[VideoInput]:
    return [
        ordered[i]
        for i in neighbour_indexes(len(ordered), index, before, after)
        if is_settled(ordered[i].age_days, min_age_days)
    ]


def channel_median_views(videos: Sequence[VideoInput], min_age_days: float) -> float:
    """Median views of the settled videos; of all videos if none is settled; 0 if empty."""
    settled = [v.views for v in videos if is_settled(v.age_days, min_age_days)]
    value = median(settled) if settled else median([v.views for v in videos])
    return value or 0.0


def channel_median_vpd(videos: Sequence[VideoInput], min_age_days: float) -> float:
    dated = [v for v in videos if v.age_days is not None]
    settled = [
        views_per_day(v.views, v.age_days) for v in dated if is_settled(v.age_days, min_age_days)
    ]
    if settled:
        value = median(settled)
    else:
        value = median([views_per_day(v.views, v.age_days) for v in dated])
    return value or 0.0


def baseline_views(
    ordered: Sequence[VideoInput],
    index: int,
    *,
    before: int = 10,
    after: int = 10,
    min_age_days: float = 7,
) -> float:
    """Median views of the settled neighbours, else the channel median, never below 1."""
    window = settled_window(ordered, index, before, after, min_age_days)
    value = median([v.views for v in window])
    if value is None:
        value = channel_median_views(ordered, min_age_days)
    return max(value, 1.0)


def window_median_vpd(
    ordered: Sequence[VideoInput],
    index: int,
    *,
    before: int = 10,
    after: int = 10,
    min_age_days: float = 7,
) -> float:
    neighbours = settled_window(ordered, index, before, after, min_age_days)
    window = [v for v in neighbours if v.age_days is not None]
    value = median([views_per_day(v.views, v.age_days) for v in window])
    if value is None:
        value = channel_median_vpd(ordered, min_age_days)
    return value


def label_for(score: float, thresholds: LabelThresholds | None = None) -> OutlierLabel:
    thresholds = thresholds or LabelThresholds()
    if score >= thresholds.one_of_ten:
        return "one-of-ten"
    if score >= thresholds.strong:
        return "strong"
    if score >= thresholds.notable:
        return "notable"
    return "normal"


def score_channel(
    videos: Iterable[VideoInput],
    *,
    before: int = 10,
    after: int = 10,
    min_age_days: float = 7,
    labels: LabelThresholds | None = None,
) -> dict[str, VideoScore]:
    """Score every video of one channel (one format) against its neighbours."""
    ordered = sorted(videos, key=lambda v: v.position)
    scores: dict[str, VideoScore] = {}
    for index, video in enumerate(ordered):
        baseline = baseline_views(
            ordered, index, before=before, after=after, min_age_days=min_age_days
        )
        vpd = views_per_day(video.views, video.age_days)
        vpd_median = window_median_vpd(
            ordered, index, before=before, after=after, min_age_days=min_age_days
        )
        score = video.views / baseline
        scores[video.video_id] = VideoScore(
            video_id=video.video_id,
            baseline_views=round(baseline, 2),
            outlier_score=round(score, 4),
            vpd=round(vpd, 2),
            vpd_ratio=round(ratio(vpd, vpd_median), 4),
            sub_ratio=round(ratio(video.views, video.followers or 0), 6),
            label=label_for(score, labels),
        )
    return scores


# Exclusions and ranking ---------------------------------------------------------------------


def exclusion_reason(
    video: VideoInput,
    fmt: ResearchFormat,
    rules: ExclusionConfig | None = None,
    *,
    used_before: bool = False,
) -> str | None:
    """Why a video can never be the pick, in plain English; ``None`` when it is eligible."""
    rules = rules or ExclusionConfig()
    if rules.live_streams and video.live_status in LIVE_STATUSES:
        return "Live stream"
    if rules.premieres and video.live_status in PREMIERE_STATUSES:
        return "Premiere or scheduled video"
    if rules.used_before and used_before:
        return "Already used by this channel"
    if video.age_days is not None and video.age_days < rules.min_age_days:
        return f"Published less than {_days(rules.min_age_days)} ago"
    if video.duration_s is not None:
        if fmt == "long":
            if video.duration_s < rules.long_min_seconds:
                return f"Shorter than {_minutes(rules.long_min_seconds)}"
            if video.duration_s > rules.long_max_seconds:
                return f"Longer than {_minutes(rules.long_max_seconds)}"
        elif video.duration_s > rules.shorts_max_seconds:
            return f"Longer than {rules.shorts_max_seconds} seconds"
    return None


def rank_key(candidate: Candidate) -> tuple[float, float, int, str]:
    return (-candidate.outlier_score, -candidate.vpd_ratio, -candidate.views, candidate.video_id)


def rank_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Eligible first, then excluded; both by score (ties by vpd_ratio); ``rank`` starts at 1."""
    rows = list(candidates)
    eligible = sorted((c for c in rows if not c.excluded_reason), key=rank_key)
    excluded = sorted((c for c in rows if c.excluded_reason), key=rank_key)
    return [
        candidate.model_copy(update={"rank": rank})
        for rank, candidate in enumerate(eligible + excluded, start=1)
    ]


def split_by_format(videos: Iterable[FlatVideo]) -> dict[ResearchFormat, list[FlatVideo]]:
    groups: dict[ResearchFormat, list[FlatVideo]] = {"long": [], "shorts": []}
    for video in videos:
        groups[video.format].append(video)
    return groups


# From listings to ranked candidates ---------------------------------------------------------


def to_video_input(video: FlatVideo, now: datetime, followers: int | None) -> VideoInput:
    return VideoInput(
        video_id=video.video_id,
        position=video.position,
        views=video.view_count or 0,
        age_days=age_days_of(video.published_at, now),
        duration_s=video.duration_s,
        followers=video.channel_follower_count or followers,
        live_status=video.live_status,
    )


def build_candidates(
    listings: Iterable[TabListing],
    fmt: ResearchFormat,
    *,
    now: datetime,
    config: ResearchConfig | None = None,
    history: UsedHistory | None = None,
    used_predicate: Callable[[FlatVideo], bool] | None = None,
) -> list[Candidate]:
    """Score every listing's videos of ``fmt`` per channel, then rank them all together."""
    config = config or ResearchConfig()
    history = history or UsedHistory()
    candidates: list[Candidate] = []
    for listing in listings:
        videos = [v for v in listing.videos if v.format == fmt]
        if not videos:
            continue
        followers = listing.channel_follower_count
        inputs = {v.video_id: to_video_input(v, now, followers) for v in videos}
        scores = score_channel(
            inputs.values(),
            before=config.neighbours_before,
            after=config.neighbours_after,
            min_age_days=config.baseline_min_age_days,
            labels=config.labels,
        )
        for video in videos:
            video_input = inputs[video.video_id]
            score = scores[video.video_id]
            used = history.contains(video.video_id, video.title) or bool(
                used_predicate and used_predicate(video)
            )
            candidates.append(
                Candidate(
                    video_id=video.video_id,
                    url=video.url,
                    title=video.title,
                    channel_name=video.channel_name or listing.channel_name,
                    channel_url=video.channel_url or listing.channel_url,
                    channel_id=video.channel_id or listing.channel_id,
                    format=fmt,
                    views=video_input.views,
                    views_exact=False,
                    published_at=video.published_at,
                    age_days=int(video_input.age_days) if video_input.age_days is not None else 0,
                    duration_s=video.duration_s,
                    baseline_views=score.baseline_views,
                    outlier_score=score.outlier_score,
                    vpd=score.vpd,
                    vpd_ratio=score.vpd_ratio,
                    sub_ratio=score.sub_ratio,
                    label=score.label,
                    thumbnail_url=video.thumbnail_url,
                    used_before=used,
                    excluded_reason=exclusion_reason(
                        video_input, fmt, config.exclude, used_before=used
                    ),
                )
            )
    return rank_candidates(candidates)


def _days(value: float) -> str:
    count = int(value) if float(value).is_integer() else value
    return f"{count} day" if count == 1 else f"{count} days"


def _minutes(seconds: int) -> str:
    minutes = round(seconds / 60, 1)
    text = f"{int(minutes)}" if minutes.is_integer() else f"{minutes:.1f}"
    return f"{text} minute" if minutes == 1 else f"{text} minutes"
