"""Outlier maths (research/outliers.py) against hand-computed expectations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cashcow_studio.models.research import Candidate, FlatVideo, TabListing
from cashcow_studio.research.config import ExclusionConfig, LabelThresholds, ResearchConfig
from cashcow_studio.research.outliers import (
    UsedHistory,
    VideoInput,
    baseline_views,
    build_candidates,
    channel_median_views,
    exclusion_reason,
    label_for,
    median,
    neighbour_indexes,
    rank_candidates,
    score_channel,
    split_by_format,
    views_per_day,
    window_median_vpd,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def video(
    video_id: str,
    position: int,
    views: int,
    age_days: float | None = 30.0,
    *,
    duration_s: float | None = 600,
    followers: int | None = 100_000,
    live_status: str | None = None,
) -> VideoInput:
    return VideoInput(
        video_id=video_id,
        position=position,
        views=views,
        age_days=age_days,
        duration_s=duration_s,
        followers=followers,
        live_status=live_status,
    )


def make_candidate(
    video_id: str,
    score: float,
    *,
    vpd_ratio: float = 1.0,
    views: int = 1000,
    age_days: int = 30,
    channel_url: str = "https://www.youtube.com/@humanember",
    channel_name: str = "Human Ember",
    channel_id: str | None = None,
    excluded_reason: str | None = None,
    used_before: bool = False,
    published: bool = True,
    title: str | None = None,
) -> Candidate:
    return Candidate(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title or f"Video {video_id}",
        channel_name=channel_name,
        channel_url=channel_url,
        channel_id=channel_id,
        format="long",
        views=views,
        published_at=NOW - timedelta(days=age_days) if published else None,
        age_days=age_days,
        duration_s=600,
        baseline_views=views / score if score else 1.0,
        outlier_score=score,
        vpd=views / max(age_days, 1),
        vpd_ratio=vpd_ratio,
        sub_ratio=0.01,
        label=label_for(score),
        used_before=used_before,
        excluded_reason=excluded_reason,
    )


def flat(
    video_id: str,
    position: int,
    views: int,
    *,
    tab: str = "videos",
    age_days: float = 30,
    duration_s: int | None = 600,
    channel_url: str = "https://www.youtube.com/@humanember",
    channel_name: str = "Human Ember",
    title: str | None = None,
    live_status: str | None = None,
) -> FlatVideo:
    return FlatVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title or f"Video {video_id}",
        channel_url=channel_url,
        channel_name=channel_name,
        tab=tab,  # type: ignore[arg-type]
        format="shorts" if tab == "shorts" else "long",
        position=position,
        duration_s=duration_s,
        view_count=views,
        published_at=NOW - timedelta(days=age_days),
        live_status=live_status,
        fetched_at=NOW,
    )


# Basic statistics ---------------------------------------------------------------------------


def test_median_handles_odd_even_and_empty() -> None:
    assert median([]) is None
    assert median([3]) == 3
    assert median([1, 3]) == 2
    assert median([5, 1, 3]) == 3
    assert median([4, 1, 3, 2]) == 2.5


def test_neighbour_indexes_cover_before_and_after_without_the_video_itself() -> None:
    assert neighbour_indexes(5, 2, 10, 10) == [0, 1, 3, 4]
    assert neighbour_indexes(5, 0, 1, 1) == [1]
    assert neighbour_indexes(5, 4, 1, 1) == [3]
    assert neighbour_indexes(1, 0, 10, 10) == []


def test_views_per_day_uses_at_least_one_day_and_zero_for_unknown_age() -> None:
    assert views_per_day(1000, 10) == 100
    assert views_per_day(500, 0.5) == 500  # max(age_days, 1)
    assert views_per_day(500, None) == 0.0


# Baselines -----------------------------------------------------------------------------------


def test_baseline_is_the_median_of_the_neighbours_by_position() -> None:
    videos = [
        video("a", 0, 100),
        video("b", 1, 200),
        video("c", 2, 1000),
        video("d", 3, 300),
        video("e", 4, 400),
    ]
    # c's neighbours: 100, 200, 300, 400 -> median 250
    assert baseline_views(videos, 2) == 250
    # a's neighbours: 200, 1000, 300, 400 -> sorted 200, 300, 400, 1000 -> (300 + 400) / 2
    assert baseline_views(videos, 0) == 350
    # e's neighbours: 100, 200, 1000, 300 -> sorted 100, 200, 300, 1000 -> 250
    assert baseline_views(videos, 4) == 250


def test_baseline_window_honours_before_and_after_counts() -> None:
    videos = [video(f"v{i}", i, 10 * i if i != 12 else 999) for i in range(25)]
    # 10 before and 10 after position 12: views 20..110 and 130..220 -> (110 + 130) / 2
    assert baseline_views(videos, 12, before=10, after=10) == 120
    # only the 10 newer videos: views 20..110 -> (60 + 70) / 2
    assert baseline_views(videos, 12, before=10, after=0) == 65
    # only the 3 older videos: 130, 140, 150 -> 140
    assert baseline_views(videos, 12, before=0, after=3) == 140


def test_baseline_ignores_neighbours_younger_than_seven_days() -> None:
    videos = [
        video("a", 0, 100),
        video("young", 1, 5000, age_days=2),
        video("c", 2, 1000),
        video("d", 3, 300),
        video("e", 4, 400),
    ]
    assert baseline_views(videos, 2) == 300  # 100, 300, 400 without the 2-day-old spike
    assert baseline_views(videos, 2, min_age_days=0) == 350  # 100, 5000, 300, 400


def test_baseline_falls_back_to_the_channel_median_when_no_neighbour_qualifies() -> None:
    videos = [video("a", 0, 10, age_days=1), video("b", 1, 20, age_days=1), video("c", 2, 90)]
    # c's neighbours are both too young: channel median of settled videos = 90
    assert baseline_views(videos, 2) == 90
    # a has one settled neighbour (c): median([90]) = 90
    assert baseline_views(videos, 0) == 90
    assert channel_median_views(videos, 7) == 90

    all_young = [video("a", 0, 10, age_days=1), video("b", 1, 20, age_days=1), video("c", 2, 40, 1)]
    # nothing is settled: median of every video = 20
    assert baseline_views(all_young, 1) == 20
    assert channel_median_views(all_young, 7) == 20


def test_baseline_is_never_below_one() -> None:
    videos = [video("a", 0, 0), video("b", 1, 0), video("c", 2, 0)]
    assert baseline_views(videos, 1) == 1.0
    scores = score_channel(videos)
    assert scores["b"].outlier_score == 0.0


# Scores ---------------------------------------------------------------------------------------


def test_score_channel_computes_every_ratio_by_hand() -> None:
    videos = [
        video("a", 0, 300, age_days=30, followers=50_000),  # vpd 10
        video("b", 1, 1000, age_days=10, followers=50_000),  # vpd 100
        video("c", 2, 600, age_days=20, followers=50_000),  # vpd 30
        video("d", 3, 2000, age_days=40, followers=50_000),  # vpd 50
    ]
    scores = score_channel(videos)
    b = scores["b"]
    assert b.baseline_views == 600  # median(300, 600, 2000)
    assert b.outlier_score == pytest.approx(1000 / 600, abs=1e-4)
    assert b.vpd == 100
    assert window_median_vpd(videos, 1) == 30  # median(10, 30, 50)
    assert b.vpd_ratio == pytest.approx(100 / 30, abs=1e-4)
    assert b.sub_ratio == pytest.approx(1000 / 50_000)
    assert b.label == "normal"


def test_score_channel_handles_unknown_ages_and_followers() -> None:
    videos = [video("a", 0, 300, age_days=None, followers=None), video("b", 1, 600, age_days=30)]
    scores = score_channel(videos)
    assert scores["a"].vpd == 0.0
    assert scores["a"].vpd_ratio == 0.0
    assert scores["a"].sub_ratio == 0.0
    assert scores["a"].baseline_views == 600  # an unknown age still counts as settled


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (10, "one-of-ten"),
        (12.5, "one-of-ten"),
        (9.99, "strong"),
        (5, "strong"),
        (4.99, "notable"),
        (3, "notable"),
        (2.99, "normal"),
        (0, "normal"),
    ],
)
def test_labels_follow_the_thresholds(score: float, label: str) -> None:
    assert label_for(score) == label


def test_labels_can_be_configured() -> None:
    thresholds = LabelThresholds(one_of_ten=20, strong=8, notable=4)
    assert label_for(10, thresholds) == "strong"
    assert label_for(4, thresholds) == "notable"


# Exclusions -----------------------------------------------------------------------------------


def test_exclusion_reasons_in_plain_english() -> None:
    rules = ExclusionConfig()
    assert exclusion_reason(video("a", 0, 10, age_days=2), "long", rules) == (
        "Published less than 3 days ago"
    )
    assert exclusion_reason(video("a", 0, 10, duration_s=200), "long", rules) == (
        "Shorter than 4 minutes"
    )
    assert exclusion_reason(video("a", 0, 10, duration_s=2500), "long", rules) == (
        "Longer than 40 minutes"
    )
    assert exclusion_reason(video("a", 0, 10, duration_s=181), "shorts", rules) == (
        "Longer than 180 seconds"
    )
    assert exclusion_reason(video("a", 0, 10, duration_s=60), "shorts", rules) is None
    assert exclusion_reason(video("a", 0, 10, live_status="is_live"), "long", rules) == (
        "Live stream"
    )
    assert exclusion_reason(video("a", 0, 10, live_status="was_live"), "long", rules) == (
        "Live stream"
    )
    assert exclusion_reason(video("a", 0, 10, live_status="is_upcoming"), "long", rules) == (
        "Premiere or scheduled video"
    )
    assert exclusion_reason(video("a", 0, 10), "long", rules, used_before=True) == (
        "Already used by this channel"
    )
    # Unknown duration or age: nothing to judge, so eligible.
    unknown = video("a", 0, 10, age_days=None, duration_s=None)
    assert exclusion_reason(unknown, "long", rules) is None
    assert exclusion_reason(video("a", 0, 10), "long", rules) is None


def test_exclusions_can_be_switched_off() -> None:
    rules = ExclusionConfig(live_streams=False, premieres=False, used_before=False)
    assert exclusion_reason(video("a", 0, 10, live_status="is_live"), "long", rules) is None
    assert exclusion_reason(video("a", 0, 10, live_status="is_upcoming"), "long", rules) is None
    assert exclusion_reason(video("a", 0, 10), "long", rules, used_before=True) is None


# Ranking --------------------------------------------------------------------------------------


def test_rank_across_competitors_by_score_then_vpd_ratio_with_excluded_last() -> None:
    rows = [
        make_candidate("a1", 8.0, vpd_ratio=1.0),
        make_candidate("b1", 8.0, vpd_ratio=2.0, channel_url="https://www.youtube.com/@b"),
        make_candidate("c1", 50.0, excluded_reason="Live stream"),
        make_candidate("a2", 3.0),
        make_candidate("b2", 1.5, channel_url="https://www.youtube.com/@b"),
    ]
    ranked = rank_candidates(rows)
    assert [c.video_id for c in ranked] == ["b1", "a1", "a2", "b2", "c1"]
    assert [c.rank for c in ranked] == [1, 2, 3, 4, 5]
    assert ranked[-1].excluded_reason == "Live stream"


def test_split_by_format_keeps_long_and_shorts_apart() -> None:
    groups = split_by_format([flat("a", 0, 10), flat("s", 0, 10, tab="shorts"), flat("b", 1, 20)])
    assert [v.video_id for v in groups["long"]] == ["a", "b"]
    assert [v.video_id for v in groups["shorts"]] == ["s"]


def test_build_candidates_scores_each_channel_and_ranks_them_together() -> None:
    ember = "https://www.youtube.com/@humanember"
    gentle = "https://www.youtube.com/@thegentlehour"
    listing_a = TabListing(
        channel_url=ember,
        tab="videos",
        channel_name="Human Ember",
        channel_follower_count=250_000,
        scanned_at=NOW,
        videos=[
            flat("a0", 0, 100, channel_url=ember),
            flat("a1", 1, 1000, channel_url=ember),  # baseline median(100, 300) = 200 -> 5.0
            flat("a2", 2, 300, channel_url=ember),
            flat("short", 3, 9_000_000, channel_url=ember, tab="shorts"),  # other format
        ],
    )
    listing_b = TabListing(
        channel_url=gentle,
        tab="videos",
        channel_name="The Gentle Hour",
        channel_follower_count=90_000,
        scanned_at=NOW,
        videos=[
            flat("b0", 0, 10, channel_url=gentle, channel_name="The Gentle Hour"),
            flat("b1", 1, 120, channel_url=gentle, channel_name="The Gentle Hour"),  # 120/10 = 12
            flat(
                "used", 2, 10, channel_url=gentle, channel_name="The Gentle Hour", title="Old One"
            ),
        ],
    )
    history = UsedHistory(video_ids=frozenset({"a0"}), titles=frozenset({"old one"}))
    candidates = build_candidates([listing_a, listing_b], "long", now=NOW, history=history)

    by_id = {c.video_id: c for c in candidates}
    assert "short" not in by_id
    assert [c.video_id for c in candidates][:2] == ["b1", "a1"]
    assert by_id["b1"].outlier_score == 12.0
    assert by_id["b1"].label == "one-of-ten"
    assert by_id["b1"].channel_name == "The Gentle Hour"
    assert by_id["a1"].outlier_score == 5.0
    assert by_id["a1"].baseline_views == 200
    assert by_id["a1"].sub_ratio == pytest.approx(1000 / 250_000)
    assert by_id["a1"].age_days == 30
    assert by_id["a0"].used_before is True
    assert by_id["a0"].excluded_reason == "Already used by this channel"
    assert by_id["used"].used_before is True  # matched by title in title_history
    assert [c.rank for c in candidates] == list(range(1, len(candidates) + 1))
    eligible = [c for c in candidates if not c.excluded_reason]
    assert all(c.rank <= len(eligible) for c in eligible)


def test_build_candidates_uses_config_for_windows_and_exclusions() -> None:
    url = "https://www.youtube.com/@humanember"
    listing = TabListing(
        channel_url=url,
        tab="videos",
        scanned_at=NOW,
        videos=[flat(f"v{i}", i, 100 if i != 2 else 400, channel_url=url) for i in range(5)],
    )
    config = ResearchConfig(
        neighbours_before=1,
        neighbours_after=1,
        exclude=ExclusionConfig(long_min_seconds=700),
    )
    candidates = build_candidates([listing], "long", now=NOW, config=config)
    top = candidates[0]
    assert top.video_id == "v2"
    assert top.outlier_score == 4.0  # neighbours v1, v3 -> median 100
    assert all(c.excluded_reason == "Shorter than 11.7 minutes" for c in candidates)
