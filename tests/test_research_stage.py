"""The mock provider, the scanner cache and the research stage, all offline."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cashflow_studio.config import Settings, load_settings
from cashflow_studio.models.channel import Channel
from cashflow_studio.models.project import Project, ProjectCreate, ProjectSource, StageName
from cashflow_studio.models.research import Tab, TabListing, TranscriptDoc, VideoDetails
from cashflow_studio.pipeline.engine import PipelineEngine
from cashflow_studio.pipeline.stages.base import StageContext, StageError
from cashflow_studio.research.cache import ResearchCache
from cashflow_studio.research.config import ExclusionConfig, ResearchConfig
from cashflow_studio.research.errors import ResearchBlocked
from cashflow_studio.research.mock import MockProvider
from cashflow_studio.research.outliers import build_candidates
from cashflow_studio.research.research_stage import ResearchStage
from cashflow_studio.research.scanner import (
    NOT_SCANNED,
    cached_outcome,
    candidates_for,
    history_for,
    scan_competitors,
)
from cashflow_studio.storage.channel_store import ChannelStore
from cashflow_studio.storage.db import connect
from conftest import AppEnv, channel_payload

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
EMBER = "https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg"
GENTLE = "https://www.youtube.com/@thegentlehour"
CALM = "https://www.youtube.com/@calmcompass"
EMBER_OUTLIER = "mkAv0000005"
CALM_VIDEO = "mkCv0000004"


@pytest.fixture
def settings(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("CFS_RESEARCH_PROVIDER", "mock")
    loaded = load_settings()
    loaded.ensure_dirs()
    return loaded


@pytest.fixture
def channel() -> Channel:
    return Channel.model_validate(channel_payload("Kind Ledger") | {"slug": "kind-ledger"})


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider(now=lambda: NOW)


@pytest.fixture
def cache(settings: Settings) -> ResearchCache:
    return ResearchCache(settings.app_data_dir)


def make_project(folder: Path, source: ProjectSource, *, project_id: str = "proj-1") -> Project:
    folder.mkdir(parents=True, exist_ok=True)
    return Project(
        id=project_id,
        channel_slug="kind-ledger",
        topic_slug="topic",
        created_at=NOW,
        updated_at=NOW,
        folder=str(folder),
        source=source,
    )


def make_ctx(
    settings: Settings,
    channel: Channel,
    project: Project,
    provider: Any,
    *,
    progress: Any = None,
    edits: dict[str, Any] | None = None,
) -> StageContext:
    kwargs: dict[str, Any] = {}
    if progress is not None:
        kwargs["progress"] = progress
    return StageContext(
        project=project,
        channel=channel,
        settings=settings,
        folder=Path(project.folder),
        providers={"research": provider},
        edits=edits or {},
        **kwargs,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# Mock fixtures --------------------------------------------------------------------------------


def test_mock_fixtures_have_three_channels_with_one_obvious_outlier_per_tab(
    provider: MockProvider,
) -> None:
    assert [c.key for c in provider.channels] == ["calm-compass", "gentle-hour", "human-ember"]
    for fixture in provider.channels:
        for tab, expected_count in (("videos", 30), ("shorts", 10)):
            listing = provider.list_tab(fixture.url, tab)
            assert len(listing.videos) == expected_count
            assert listing.channel_follower_count == fixture.followers
            fmt = "long" if tab == "videos" else "shorts"
            candidates = build_candidates([listing], fmt, now=NOW)
            eligible = [c for c in candidates if not c.excluded_reason]
            top = [c for c in eligible if c.label == "one-of-ten"]
            assert len(top) == 1, (fixture.key, tab, [c.outlier_score for c in eligible[:3]])
            assert candidates[0].video_id == top[0].video_id
            assert candidates[0].outlier_score >= 10
        long_reasons = {
            c.video_id: c.excluded_reason
            for c in build_candidates([provider.list_tab(fixture.url, "videos")], "long", now=NOW)
            if c.excluded_reason
        }
        assert set(long_reasons.values()) == {
            "Published less than 3 days ago",
            "Longer than 40 minutes",
            "Live stream",
        }


def test_mock_provider_is_deterministic_and_maps_any_competitor_url(
    provider: MockProvider, tmp_path: Path
) -> None:
    assert provider.fixture_for(EMBER).key == "human-ember"  # alias of @humanember
    assert provider.fixture_for(GENTLE).key == "gentle-hour"
    # Unknown links take the first fixture not used yet, then start over in file order.
    assert provider.fixture_for("https://www.youtube.com/@someone-else").key == "calm-compass"
    assert provider.fixture_for("https://www.youtube.com/@a-fourth-one").key == "calm-compass"
    assert provider.fixture_for("https://www.youtube.com/@someone-else").key == "calm-compass"
    first = provider.list_tab(EMBER, "videos")
    second = MockProvider(now=lambda: NOW).list_tab(EMBER, "videos")
    assert first.model_dump() == second.model_dump()

    details = provider.video_details(EMBER_OUTLIER)
    assert details.view_count == 640000 + 5 * 13 + 7  # 16 x 40000 base, plus the exact delta
    assert details.caption_languages.auto == ["en"]
    transcript = provider.transcript(EMBER_OUTLIER, "en")
    assert transcript.source == "mock" and len(transcript.words) > 100
    assert transcript.words[1].start == pytest.approx(0.4)
    saved = provider.thumbnail(EMBER_OUTLIER, tmp_path / "t.jpg")
    assert saved.read_bytes()[:2] == b"\xff\xd8"


# Scanner and cache ----------------------------------------------------------------------------


def test_scan_reuses_the_cache_within_the_hour_unless_forced(
    channel: Channel, provider: MockProvider, cache: ResearchCache
) -> None:
    config = ResearchConfig(cache_minutes=60)
    first = scan_competitors(channel, provider, cache, config=config, tabs=["videos"], now=NOW)
    assert len(first.listings) == 2 and first.videos_found == 60
    assert all(not listing.from_cache for listing in first.listings)
    assert [s.videos_found for s in first.statuses] == [30, 30]
    assert first.statuses[0].id == "UCFjva5hxOFoj2ViNgSuEQJg"
    assert len(provider.calls) == 2

    later = NOW + timedelta(minutes=30)
    second = scan_competitors(channel, provider, cache, config=config, tabs=["videos"], now=later)
    assert len(provider.calls) == 2  # nothing was listed again
    assert all(listing.from_cache for listing in second.listings)
    assert second.videos_found == 60

    scan_competitors(
        channel, provider, cache, config=config, tabs=["videos"], now=later, force=True
    )
    assert len(provider.calls) == 4

    stale = NOW + timedelta(minutes=61)
    scan_competitors(channel, provider, cache, config=config, tabs=["videos"], now=stale)
    assert len(provider.calls) == 6


def test_scan_records_a_failing_competitor_and_keeps_its_last_good_listing(
    channel: Channel, cache: ResearchCache
) -> None:
    config = ResearchConfig(cache_minutes=0)
    good = MockProvider(now=lambda: NOW)
    scan_competitors(channel, good, cache, config=config, tabs=["videos"], now=NOW)

    failing = MockProvider(now=lambda: NOW, failing_urls={GENTLE})
    outcome = scan_competitors(channel, failing, cache, config=config, tabs=["videos"], now=NOW)
    assert outcome.statuses[0].error is None
    assert "did not answer" in (outcome.statuses[1].error or "")
    assert outcome.statuses[1].videos_found == 30  # the earlier listing is still used
    assert len(outcome.listings) == 2
    assert cache.get_scan(GENTLE, "videos").error is not None

    fresh_cache = ResearchCache(cache.app_data_dir / "other")
    outcome = scan_competitors(
        channel, failing, fresh_cache, config=config, tabs=["videos"], now=NOW
    )
    assert len(outcome.listings) == 1 and outcome.statuses[1].videos_found == 0
    assert fresh_cache.load_listing(GENTLE, "videos") is None


def test_cached_outcome_reports_competitors_that_were_never_scanned(
    channel: Channel, provider: MockProvider, cache: ResearchCache
) -> None:
    empty = cached_outcome(channel, cache, tabs=["videos"])
    assert empty.scanned_at is None and empty.listings == []
    assert [s.error for s in empty.statuses] == [NOT_SCANNED, NOT_SCANNED]

    scan_competitors(channel, provider, cache, config=ResearchConfig(), tabs=["videos"], now=NOW)
    loaded = cached_outcome(channel, cache, tabs=["videos"])
    assert loaded.scanned_at == NOW
    assert [s.error for s in loaded.statuses] == [None, None]
    assert loaded.videos_found == 60
    assert all(listing.from_cache for listing in loaded.listings)
    ranked = candidates_for(loaded, "long", now=NOW)
    assert ranked[0].video_id == EMBER_OUTLIER


def test_history_combines_recorded_picks_with_the_title_history_table(
    cache: ResearchCache,
) -> None:
    cache.record_pick("kind-ledger", "vid-a", "proj-1", NOW)
    cache.record_pick("kind-ledger", "vid-b", "proj-2", NOW)
    cache.record_pick("other-channel", "vid-c", "proj-3", NOW)
    with connect(cache.app_data_dir) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS title_history "
            "(channel_slug TEXT, title TEXT, project_id TEXT)"
        )
        conn.execute(
            "INSERT INTO title_history VALUES (?, ?, ?)", ("kind-ledger", "  Done Before ", "p")
        )
    history = history_for(cache, "kind-ledger")
    assert history.video_ids == {"vid-a", "vid-b"}
    assert history.titles == {"done before"}
    assert history_for(cache, "kind-ledger", exclude_project_id="proj-1").video_ids == {"vid-b"}
    assert history_for(cache, "nobody").video_ids == set()


def test_cache_keeps_details_and_their_age(cache: ResearchCache, provider: MockProvider) -> None:
    details = provider.video_details(EMBER_OUTLIER)
    cache.save_details(details)
    assert cache.get_details(EMBER_OUTLIER) == details
    soon = NOW + timedelta(minutes=30)
    late = NOW + timedelta(minutes=61)
    assert cache.get_details(EMBER_OUTLIER, max_age_minutes=60, now=soon) == details
    assert cache.get_details(EMBER_OUTLIER, max_age_minutes=60, now=late) is None
    assert cache.get_details("unknown-id") is None


# Stage ----------------------------------------------------------------------------------------


def test_stage_ai_pick_writes_every_file_and_the_review_payload(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    messages: list[tuple[str, float | None]] = []
    ctx = make_ctx(
        settings, channel, project, provider, progress=lambda m, p=None: messages.append((m, p))
    )
    stage = ResearchStage(now=lambda: NOW)

    result = run(stage.run(ctx))

    research_dir = tmp_path / "p1" / "01_research"
    assert sorted(p.name for p in research_dir.iterdir()) == [
        "candidates.json",
        "competitor_thumbnail.jpg",
        "pick.json",
        "transcript.json",
        "video.json",
    ]
    assert [p.name for p in result.outputs] == [
        "candidates.json", "pick.json", "video.json", "transcript.json", "competitor_thumbnail.jpg"
    ]
    pick = read_json(research_dir / "pick.json")
    assert pick["kind"] == "ai_pick" and pick["strategy"] == "top_outlier_fresh"
    assert pick["video_id"] == EMBER_OUTLIER
    assert pick["candidate"]["label"] == "one-of-ten"
    assert pick["candidate"]["views_exact"] is True
    assert pick["reason"].startswith("Highest outlier score")
    candidates = read_json(research_dir / "candidates.json")
    assert candidates["format"] == "long" and len(candidates["candidates"]) == 60
    assert [c["name"] for c in candidates["channels"]] == ["Human Ember", "The Gentle Hour"]
    video = read_json(research_dir / "video.json")
    assert video["video_id"] == EMBER_OUTLIER and video["like_count"] > 0
    transcript = TranscriptDoc.model_validate(read_json(research_dir / "transcript.json"))
    assert transcript.available and transcript.language == "en"
    assert (research_dir / "competitor_thumbnail.jpg").read_bytes()[:2] == b"\xff\xd8"

    payload = result.needs_review_payload
    assert payload["stage"] == "research" and payload["source_kind"] == "ai_pick"
    assert payload["pick_video_id"] == EMBER_OUTLIER
    assert payload["pick"]["video_id"] == EMBER_OUTLIER
    assert len(payload["candidates"]) == 50
    assert payload["candidates"][0]["video_id"] == EMBER_OUTLIER
    assert payload["transcript_available"] is True
    assert payload["thumbnail_file"] == "competitor_thumbnail.jpg"
    assert payload["notes"] == []
    assert project.title == "The stranger who paid for every meal in the diner"
    assert "Picked 'The stranger who paid for every meal in the diner'" in result.summary
    assert messages[0] == ("Reading Human Ember (videos)", 0.0)
    assert messages[-1] == ("Research done", 100.0)
    # The pick is remembered for the channel so it is never used twice.
    assert ResearchCache(settings.app_data_dir).used_video_ids("kind-ledger") == {EMBER_OUTLIER}


def test_stage_never_picks_the_same_video_twice_for_a_channel(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    stage = ResearchStage(now=lambda: NOW)
    first = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"), project_id="proj-1")
    run(stage.run(make_ctx(settings, channel, first, provider)))
    second = make_project(tmp_path / "p2", ProjectSource(kind="ai_pick"), project_id="proj-2")
    result = run(stage.run(make_ctx(settings, channel, second, provider)))
    assert result.needs_review_payload["pick_video_id"] != EMBER_OUTLIER
    # Excluded videos rank last, so the used one sits outside the top 50 of the review payload
    # but is still in candidates.json with the reason.
    assert all(c["video_id"] != EMBER_OUTLIER for c in result.needs_review_payload["candidates"])
    everything = read_json(tmp_path / "p2" / "01_research" / "candidates.json")["candidates"]
    used = [c for c in everything if c["video_id"] == EMBER_OUTLIER]
    assert used and used[0]["used_before"] is True
    assert used[0]["excluded_reason"] == "Already used by this channel"
    assert used[0]["rank"] > 50
    # Running the first project again is a redo: the competitors are listed afresh and the
    # AI proposes a video it has not picked for this project before.
    listings_before = sum(1 for call in provider.calls if call[0] == "list_tab")
    again = run(stage.run(make_ctx(settings, channel, first, provider)))
    new_pick = again.needs_review_payload["pick_video_id"]
    assert new_pick not in {EMBER_OUTLIER, result.needs_review_payload["pick_video_id"]}
    assert sum(1 for call in provider.calls if call[0] == "list_tab") > listings_before
    rejected = read_json(tmp_path / "p1" / "01_research" / "rejected.json")
    assert rejected == {"video_ids": [EMBER_OUTLIER]}
    assert any("left out this time" in n for n in again.needs_review_payload["notes"])
    # Only the current pick of each project counts as used; the rejected one is free again.
    used_now = ResearchCache(settings.app_data_dir).used_video_ids("kind-ledger")
    assert used_now == {new_pick, result.needs_review_payload["pick_video_id"]}


def test_stage_manual_pick_uses_the_chosen_video_even_outside_the_competitor_list(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    stage = ResearchStage(now=lambda: NOW)
    listed = make_project(
        tmp_path / "p1", ProjectSource(kind="manual_pick", video_id="mkBv0000003")
    )
    result = run(stage.run(make_ctx(settings, channel, listed, provider)))
    pick = read_json(tmp_path / "p1" / "01_research" / "pick.json")
    assert pick["kind"] == "manual_pick" and pick["strategy"] == "person"
    assert pick["video_id"] == "mkBv0000003"
    assert result.needs_review_payload["pick"]["rank"] > 1
    assert listed.title == pick["title"]

    outside = make_project(tmp_path / "p2", ProjectSource(kind="manual_pick", video_id=CALM_VIDEO))
    result = run(stage.run(make_ctx(settings, channel, outside, provider)))
    payload = result.needs_review_payload
    assert payload["pick_video_id"] == CALM_VIDEO
    assert payload["pick"]["channel_name"] == "Calm Compass"
    assert payload["pick"]["rank"] == 0 and payload["pick"]["views_exact"] is True
    assert payload["candidates"][-1]["video_id"] == CALM_VIDEO  # highlighted even if not listed
    assert VideoDetails.model_validate(read_json(tmp_path / "p2" / "01_research" / "video.json"))

    missing = make_project(
        tmp_path / "p3", ProjectSource(kind="manual_pick", video_id="nope0000000")
    )
    with pytest.raises(StageError, match="could not be read"):
        run(stage.run(make_ctx(settings, channel, missing, provider)))


def test_stage_own_topic_skips_scanning(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    project = make_project(
        tmp_path / "own", ProjectSource(kind="own_topic", topic_text="Why nobody waits anymore")
    )
    result = run(ResearchStage(now=lambda: NOW).run(make_ctx(settings, channel, project, provider)))
    assert provider.calls == []
    assert [p.name for p in result.outputs] == ["pick.json"]
    pick = read_json(tmp_path / "own" / "01_research" / "pick.json")
    assert pick["kind"] == "own_topic" and pick["topic_text"] == "Why nobody waits anymore"
    assert pick["video_id"] is None
    assert result.needs_review_payload["source_kind"] == "own_topic"
    assert result.needs_review_payload["candidates"] == []
    assert project.title == "Why nobody waits anymore"
    assert "No competitor video was scanned" in result.summary

    blank = make_project(tmp_path / "blank", ProjectSource(kind="own_topic", topic_text="  "))
    with pytest.raises(StageError, match="Type the topic"):
        run(ResearchStage().run(make_ctx(settings, channel, blank, provider)))


def test_stage_notes_a_failing_competitor_but_still_picks(
    settings: Settings, channel: Channel, tmp_path: Path
) -> None:
    provider = MockProvider(now=lambda: NOW, failing_urls={GENTLE})
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    result = run(ResearchStage(now=lambda: NOW).run(make_ctx(settings, channel, project, provider)))
    payload = result.needs_review_payload
    assert payload["pick_video_id"] == EMBER_OUTLIER
    assert len(payload["notes"]) == 1 and payload["notes"][0].startswith("The Gentle Hour:")
    assert [c["videos_found"] for c in payload["channels"]] == [30, 0]


def test_stage_fails_plainly_when_nothing_is_eligible_or_youtube_blocks(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    class Blocked:
        name = "blocked"

        def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
            raise ResearchBlocked("YouTube is asking this computer to slow down or sign in.")

    # Nothing is cached yet, so the very first listing hits the block.
    other = make_project(tmp_path / "p2", ProjectSource(kind="ai_pick"))
    with pytest.raises(StageError, match="slow down or sign in"):
        run(ResearchStage(now=lambda: NOW).run(make_ctx(settings, channel, other, Blocked())))
    assert not (tmp_path / "p2" / "01_research" / "candidates.json").exists()

    strict = ResearchConfig(exclude=ExclusionConfig(min_age_days=10_000))
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    strict_stage = ResearchStage(config=strict, now=lambda: NOW)
    with pytest.raises(StageError, match="None of the competitor videos can be used"):
        run(strict_stage.run(make_ctx(settings, channel, project, provider)))
    assert (tmp_path / "p1" / "01_research" / "candidates.json").is_file()  # still written

    lonely = channel.model_copy(update={"competitors": []})
    third = make_project(tmp_path / "p3", ProjectSource(kind="ai_pick"))
    with pytest.raises(StageError, match="no competitor channels"):
        run(ResearchStage(now=lambda: NOW).run(make_ctx(settings, lonely, third, provider)))


def test_apply_edits_changes_the_pick_without_rescanning(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    stage = ResearchStage(now=lambda: NOW)
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    ctx = make_ctx(settings, channel, project, provider)
    run(stage.run(ctx))
    listings_before = sum(1 for call in provider.calls if call[0] == "list_tab")

    assert run(stage.apply_edits(ctx)) is None  # nothing to apply
    ctx.edits = {"video_id": "mkBv0000012"}
    result = run(stage.apply_edits(ctx))
    assert result is not None
    assert result.needs_review_payload["pick_video_id"] == "mkBv0000012"
    assert result.needs_review_payload["source_kind"] == "manual_pick"
    pick = read_json(tmp_path / "p1" / "01_research" / "pick.json")
    assert pick["video_id"] == "mkBv0000012" and pick["strategy"] == "person"
    assert read_json(tmp_path / "p1" / "01_research" / "video.json")["video_id"] == "mkBv0000012"
    assert project.title == pick["title"]
    assert sum(1 for call in provider.calls if call[0] == "list_tab") == listings_before


def test_async_progress_callbacks_are_awaited_on_the_event_loop(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    seen: list[str] = []

    async def progress(message: str, pct: float | None = None) -> None:
        seen.append(message)

    async def scenario() -> None:
        project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
        ctx = make_ctx(settings, channel, project, provider, progress=progress)
        await ResearchStage(now=lambda: NOW).run(ctx)
        for _ in range(5):
            await asyncio.sleep(0)

    run(scenario())
    assert seen[0] == "Reading Human Ember (videos)"
    assert "Research done" in seen


def test_stage_reads_the_provider_from_the_environment_when_none_is_given(
    settings: Settings, channel: Channel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CFS_RESEARCH_PROVIDER", "mock")
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    ctx = StageContext(
        project=project, channel=channel, settings=settings, folder=Path(project.folder)
    )
    result = run(ResearchStage(now=lambda: NOW).run(ctx))
    assert result.needs_review_payload["pick_video_id"] == EMBER_OUTLIER


def test_apply_edits_records_the_person_as_the_source_and_a_redo_keeps_it(
    settings: Settings, channel: Channel, provider: MockProvider, tmp_path: Path
) -> None:
    stage = ResearchStage(now=lambda: NOW)
    project = make_project(tmp_path / "p1", ProjectSource(kind="ai_pick"))
    ctx = make_ctx(settings, channel, project, provider)
    run(stage.run(ctx))
    assert ResearchCache(settings.app_data_dir).used_video_ids("kind-ledger") == {EMBER_OUTLIER}

    ctx.edits = {"video_id": "mkBv0000012"}
    run(stage.apply_edits(ctx))
    # job.json now says a person chose the video: the title stage, the project page and a
    # redo all see manual_pick, and the overridden AI pick is not "used" any more.
    assert project.source.kind == "manual_pick"
    assert project.source.video_id == "mkBv0000012"
    assert project.source.video_url == "https://www.youtube.com/watch?v=mkBv0000012"
    assert ResearchCache(settings.app_data_dir).used_video_ids("kind-ledger") == {"mkBv0000012"}

    ctx.edits = {}
    again = run(stage.run(ctx))  # what the engine does on a redo
    assert again.needs_review_payload["pick_video_id"] == "mkBv0000012"
    pick = read_json(tmp_path / "p1" / "01_research" / "pick.json")
    assert pick["kind"] == "manual_pick" and pick["video_id"] == "mkBv0000012"


class SlowTranscriptProvider:
    """The mock provider with a slow transcript download, to archive a project mid-run."""

    name = "slow"

    def __init__(self, inner: MockProvider, delay_s: float) -> None:
        self.inner = inner
        self.delay_s = delay_s

    def transcript(self, video_id: str, lang: str) -> TranscriptDoc:
        time.sleep(self.delay_s)
        return self.inner.transcript(video_id, lang)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_archive_during_research_stops_the_worker_and_forgets_the_pick(
    settings: Settings, channel: Channel, provider: MockProvider
) -> None:
    ChannelStore(settings.resolved_shared_dir).create(channel)
    slow = SlowTranscriptProvider(provider, 0.8)

    async def main() -> None:
        engine = PipelineEngine(settings, providers={"research": slow})
        engine.register(ResearchStage(now=lambda: NOW))
        body = ProjectCreate(
            channel_slug=channel.slug,
            format="long",
            source=ProjectSource(kind="ai_pick"),
            stage_mode_overrides={StageName.research: "auto"},
        )
        project = engine.create_project(body, channel)
        folder = Path(project.folder)
        await engine.run(project.id)
        await asyncio.sleep(0.3)
        assert engine.is_busy(project.id)
        destination = await engine.archive(project.id)
        assert not folder.exists() and (destination / "job.json").is_file()
        # The worker thread noticed the cancel flag and wrote nothing more: no ghost folder,
        # no pick.json, no "used" video for a project that no longer exists.
        await asyncio.sleep(1.5)
        assert not folder.exists()
        assert not (destination / "01_research" / "pick.json").exists()
        assert ResearchCache(settings.app_data_dir).used_video_ids(channel.slug) == set()
        assert engine.store.list() == []
        assert engine.tasks == {} and not engine.is_busy(project.id)

    asyncio.run(main())
