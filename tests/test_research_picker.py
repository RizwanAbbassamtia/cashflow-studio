"""The AI pick (research/picker.py): exclusions, preferences and the rerank hook."""

from __future__ import annotations

import pytest

from cashcow_studio.models.channel import Channel
from cashcow_studio.models.research import Candidate
from cashcow_studio.research.outliers import UsedHistory
from cashcow_studio.research.picker import (
    competitor_language,
    eligible_candidates,
    explain_pick,
    normalise_channel_url,
    pick_one,
    rerank_enabled,
)
from test_research_outliers import make_candidate

EMBER = "https://www.youtube.com/@humanember"
CALM = "https://www.youtube.com/@calmcompass"


@pytest.fixture
def channel() -> Channel:
    return Channel.model_validate(
        {
            "slug": "kind-ledger",
            "channel": {"name": "Kind Ledger", "language": "English"},
            "competitors": [
                {"name": "Human Ember", "url": EMBER, "language": "English"},
                {"name": "Calm Compass", "url": CALM, "language": "Spanish"},
            ],
        }
    )


def test_pick_skips_excluded_and_used_candidates(channel: Channel) -> None:
    rows = [
        make_candidate("live", 50.0, excluded_reason="Live stream"),
        make_candidate("used", 40.0, used_before=True),
        make_candidate("best", 9.0),
        make_candidate("ok", 4.0),
    ]
    assert [c.video_id for c in eligible_candidates(rows)] == ["best", "ok"]
    pick = pick_one(rows, channel)
    assert pick is not None and pick.video_id == "best"


def test_pick_skips_videos_in_the_channel_history(channel: Channel) -> None:
    rows = [
        make_candidate("a", 9.0, title="Done Before"),
        make_candidate("b", 8.0),
        make_candidate("c", 7.0),
    ]
    by_id = pick_one(rows, channel, UsedHistory(video_ids=frozenset({"a"})))
    assert by_id is not None and by_id.video_id == "b"
    by_title = pick_one(rows, channel, UsedHistory(titles=frozenset({"done before"})))
    assert by_title is not None and by_title.video_id == "b"


def test_pick_prefers_competitors_in_the_channel_language(channel: Channel) -> None:
    rows = [
        make_candidate("spanish", 9.0, channel_url=CALM, channel_name="Calm Compass"),
        make_candidate("english", 5.0, channel_url=EMBER),
    ]
    pick = pick_one(rows, channel)
    assert pick is not None and pick.video_id == "english"
    # With no English candidate the preference is dropped rather than picking nothing.
    only_spanish = [rows[0]]
    pick = pick_one(only_spanish, channel)
    assert pick is not None and pick.video_id == "spanish"


def test_pick_prefers_the_last_90_days(channel: Channel) -> None:
    rows = [make_candidate("old", 9.0, age_days=200), make_candidate("fresh", 4.0, age_days=30)]
    pick = pick_one(rows, channel)
    assert pick is not None and pick.video_id == "fresh"
    pick = pick_one(rows, channel, fresh_days=365)
    assert pick is not None and pick.video_id == "old"
    all_old = [make_candidate("old", 9.0, age_days=200), make_candidate("older", 3.0, age_days=400)]
    pick = pick_one(all_old, channel)
    assert pick is not None and pick.video_id == "old"


def test_pick_breaks_score_ties_by_views_per_day_ratio(channel: Channel) -> None:
    rows = [make_candidate("slow", 6.0, vpd_ratio=1.0), make_candidate("fast", 6.0, vpd_ratio=3.0)]
    pick = pick_one(rows, channel)
    assert pick is not None and pick.video_id == "fast"


def test_pick_returns_none_when_nothing_is_eligible(channel: Channel) -> None:
    rows = [
        make_candidate("a", 9.0, excluded_reason="Live stream"),
        make_candidate("b", 8.0, used_before=True),
    ]
    assert pick_one(rows, channel) is None
    assert pick_one([], channel) is None


def test_unknown_strategy_is_refused(channel: Channel) -> None:
    with pytest.raises(ValueError, match="Unknown pick strategy"):
        pick_one([make_candidate("a", 9.0)], channel, strategy="random")


def test_rerank_hook_chooses_among_the_top_candidates(channel: Channel) -> None:
    rows = [
        make_candidate("first", 9.0),
        make_candidate("second", 8.0),
        make_candidate("third", 7.0),
    ]
    seen: list[list[str]] = []

    def rerank(top: list[Candidate], ch: Channel) -> list[str]:
        seen.append([c.video_id for c in top])
        assert ch.slug == "kind-ledger"
        return ["second", "first"]

    pick = pick_one(rows, channel, rerank=rerank, rerank_top=2)
    assert pick is not None and pick.video_id == "second"
    assert seen == [["first", "second"]]  # only the top two were offered


def test_rerank_failures_and_unknown_ids_fall_back_to_the_score_order(channel: Channel) -> None:
    rows = [make_candidate("first", 9.0), make_candidate("second", 8.0)]

    def broken(top: list[Candidate], ch: Channel) -> list[str]:
        raise RuntimeError("model unavailable")

    pick = pick_one(rows, channel, rerank=broken)
    assert pick is not None and pick.video_id == "first"
    pick = pick_one(rows, channel, rerank=lambda top, ch: ["nobody"])
    assert pick is not None and pick.video_id == "first"
    pick = pick_one(rows, channel, rerank=lambda top, ch: None)
    assert pick is not None and pick.video_id == "first"


def test_rerank_is_off_unless_the_environment_switch_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CCS_RESEARCH_LLM_RERANK", raising=False)
    assert rerank_enabled() is False
    monkeypatch.setenv("CCS_RESEARCH_LLM_RERANK", "0")
    assert rerank_enabled() is False
    monkeypatch.setenv("CCS_RESEARCH_LLM_RERANK", "1")
    assert rerank_enabled() is True


def test_competitor_language_matches_urls_loosely_and_by_channel_id(channel: Channel) -> None:
    loud = "HTTPS://WWW.YouTube.com/@HumanEmber/"
    assert normalise_channel_url(loud) == "youtube.com/@humanember"
    upper = make_candidate("a", 1.0, channel_url=EMBER.upper())
    assert competitor_language(channel, upper) == "English"
    assert competitor_language(channel, make_candidate("a", 1.0, channel_url=CALM)) == "Spanish"
    unknown = make_candidate("a", 1.0, channel_url="https://youtube.com/@x")
    assert competitor_language(channel, unknown) is None
    channel.competitors[0].id = "UCFjva5hxOFoj2ViNgSuEQJg"
    stranger = make_candidate(
        "a", 1.0, channel_url="https://youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg",
        channel_id="UCFjva5hxOFoj2ViNgSuEQJg",
    )
    assert competitor_language(channel, stranger) == "English"


def test_explain_pick_is_one_plain_sentence() -> None:
    text = explain_pick(make_candidate("a", 12.3, age_days=41))
    assert text == (
        "Highest outlier score: 12.3x its channel's usual views (one-of-ten), "
        "published 41 days ago on Human Ember."
    )
    assert explain_pick(make_candidate("a", 4.0, published=False), reranked=True).startswith(
        "Chosen by the AI rerank"
    )
