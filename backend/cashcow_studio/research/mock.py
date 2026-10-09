"""Offline research provider (``CCS_RESEARCH_PROVIDER=mock``): deterministic fixtures.

The fixtures live in ``tests/fixtures/research/channels/*.json``: three competitor channels
with 30 long-form videos and 10 Shorts each, one obvious outlier (10x or more) per tab, a
too-new video, an over-long video and a recorded live stream so the exclusions have
something to bite on. Each fixture video stores ``days_ago`` rather than a date, so the
ages are stable relative to "now" whenever the tests run.

A competitor URL that matches a fixture's ``url`` or ``aliases`` gets that fixture; any
other URL is assigned the next fixture in order (cycling), so any channel's competitor list
works offline. Details, transcripts and the thumbnail are derived from the same data.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from ..models.research import (
    CaptionLanguages,
    Chapter,
    FlatVideo,
    Tab,
    TabListing,
    TranscriptDoc,
    TranscriptSegment,
    VideoDetails,
    format_for_tab,
)
from .config import repo_root
from .errors import ResearchError, VideoNotFound
from .languages import language_code
from .picker import normalise_channel_url
from .ytdlp_client import WATCH_URL, spread_words

FIXTURES_ENV = "CCS_RESEARCH_FIXTURES"
CHANNELS_DIR = "channels"
THUMBNAIL_FILE = "thumbnail.jpg"
WORD_SECONDS = 0.4


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_fixtures_dir() -> Path:
    override = os.environ.get(FIXTURES_ENV, "").strip()
    return Path(override) if override else repo_root() / "tests" / "fixtures" / "research"


class FixtureVideo(BaseModel):
    video_id: str
    title: str
    tab: Tab
    position: int
    duration_s: int
    view_count: int
    days_ago: float
    live_status: str | None = None


class FixtureChannel(BaseModel):
    key: str
    name: str
    url: str
    aliases: list[str] = []
    channel_id: str
    followers: int
    language: str = "English"
    videos: list[FixtureVideo]

    def matches(self, channel_url: str) -> bool:
        wanted = normalise_channel_url(channel_url)
        return any(normalise_channel_url(u) == wanted for u in (self.url, *self.aliases))


def load_fixture_channels(fixtures_dir: Path) -> list[FixtureChannel]:
    folder = Path(fixtures_dir) / CHANNELS_DIR
    files = sorted(folder.glob("*.json"))
    if not files:
        raise ResearchError(
            f"The mock research fixtures are missing: no JSON files in {folder}. "
            "Set CCS_RESEARCH_FIXTURES to the folder that holds them."
        )
    return [FixtureChannel.model_validate_json(f.read_text(encoding="utf-8")) for f in files]


class MockProvider:
    name = "mock"

    def __init__(
        self,
        fixtures_dir: Path | None = None,
        *,
        now: Callable[[], datetime] = utc_now,
        failing_urls: set[str] | None = None,
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir or default_fixtures_dir())
        self.channels = load_fixture_channels(self.fixtures_dir)
        self._now = now
        self._assigned: dict[str, FixtureChannel] = {}
        self.failing_urls = {normalise_channel_url(u) for u in (failing_urls or set())}
        """Competitor URLs that fail to list (tests of the per-channel error path)."""
        self.calls: list[tuple[str, str]] = []

    # Fixture lookup ------------------------------------------------------------------------

    def fixture_for(self, channel_url: str) -> FixtureChannel:
        key = normalise_channel_url(channel_url)
        if key in self._assigned:
            return self._assigned[key]
        for fixture in self.channels:
            if fixture.matches(channel_url):
                self._assigned[key] = fixture
                return fixture
        used = {id(f) for f in self._assigned.values()}
        for fixture in self.channels:
            if id(fixture) not in used:
                self._assigned[key] = fixture
                return fixture
        fixture = self.channels[len(self._assigned) % len(self.channels)]
        self._assigned[key] = fixture
        return fixture

    def _find_video(self, video_id: str) -> tuple[FixtureChannel, FixtureVideo]:
        for fixture in self.channels:
            for video in fixture.videos:
                if video.video_id == video_id:
                    return fixture, video
        raise VideoNotFound(f"The video {video_id} is not in the mock fixtures.")

    def _published(self, video: FixtureVideo) -> datetime:
        return self._now() - timedelta(days=video.days_ago)

    # Provider API --------------------------------------------------------------------------

    def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
        self.calls.append(("list_tab", f"{channel_url}/{tab}"))
        if normalise_channel_url(channel_url) in self.failing_urls:
            raise ResearchError(
                f"YouTube did not answer while listing {channel_url}/{tab}: "
                "This channel does not have a videos tab"
            )
        fixture = self.fixture_for(channel_url)
        now = self._now()
        videos = sorted((v for v in fixture.videos if v.tab == tab), key=lambda v: v.position)
        entries = [
            FlatVideo(
                video_id=video.video_id,
                url=WATCH_URL.format(video_id=video.video_id),
                title=video.title,
                channel_url=channel_url,
                channel_id=fixture.channel_id,
                channel_name=fixture.name,
                tab=tab,
                format=format_for_tab(tab),
                position=video.position,
                duration_s=video.duration_s,
                view_count=video.view_count,
                published_at=self._published(video),
                thumbnail_url=f"https://i.ytimg.com/vi/{video.video_id}/hqdefault.jpg",
                live_status=video.live_status,
                channel_follower_count=fixture.followers,
                fetched_at=now,
            )
            for video in videos[:max_videos]
        ]
        return TabListing(
            channel_url=channel_url,
            tab=tab,
            channel_id=fixture.channel_id,
            channel_name=fixture.name,
            channel_follower_count=fixture.followers,
            scanned_at=now,
            videos=entries,
        )

    def video_details(self, video_id: str) -> VideoDetails:
        self.calls.append(("video_details", video_id))
        fixture, video = self._find_video(video_id)
        published = self._published(video)
        exact_views = video.view_count + video.position * 13 + 7
        words = [w.strip(".,!?").lower() for w in video.title.split()]
        tags = [w for w in words if len(w) > 3][:8]
        thirds = max(video.duration_s // 3, 1)
        return VideoDetails(
            video_id=video.video_id,
            url=WATCH_URL.format(video_id=video.video_id),
            title=video.title,
            channel_id=fixture.channel_id,
            channel_name=fixture.name,
            channel_url=fixture.url,
            channel_follower_count=fixture.followers,
            duration_s=video.duration_s,
            view_count=exact_views,
            like_count=exact_views // 40,
            comment_count=exact_views // 400,
            upload_date=published.date(),
            published_at=published,
            tags=tags,
            categories=["People & Blogs"],
            description=f"{video.title}. A mock description from the offline fixtures.",
            chapters=[
                Chapter(title="Intro", start_s=0, end_s=thirds),
                Chapter(title="The story", start_s=thirds, end_s=2 * thirds),
                Chapter(title="What it means", start_s=2 * thirds, end_s=video.duration_s),
            ],
            caption_languages=CaptionLanguages(manual=[], auto=[language_code(fixture.language)]),
            thumbnail_url=f"https://i.ytimg.com/vi/{video.video_id}/maxresdefault.jpg",
            live_status=video.live_status,
            language=language_code(fixture.language),
            fetched_at=self._now(),
        )

    def transcript(self, video_id: str, lang: str) -> TranscriptDoc:
        self.calls.append(("transcript", video_id))
        fixture, video = self._find_video(video_id)
        sentences = mock_sentences(video.title, fixture.name)
        segments: list[TranscriptSegment] = []
        cursor = 0.0
        for sentence in sentences:
            length = WORD_SECONDS * len(sentence.split())
            segments.append(
                TranscriptSegment(
                    start=round(cursor, 3), end=round(cursor + length, 3), text=sentence
                )
            )
            cursor += length
        words = [w for s in segments for w in spread_words(s.text, s.start, s.end)]
        return TranscriptDoc(
            video_id=video_id,
            language=language_code(fixture.language),
            source="mock",
            kind="auto",
            segments=segments,
            words=words,
            text=" ".join(sentences),
        )

    def thumbnail(self, video_id: str, dest: Path) -> Path:
        self.calls.append(("thumbnail", video_id))
        self._find_video(video_id)
        source = self.fixtures_dir / THUMBNAIL_FILE
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copyfile(source, dest)
        else:
            dest.write_bytes(MINIMAL_JPEG)
        return dest


def mock_sentences(title: str, channel_name: str) -> list[str]:
    """A short, deterministic narration built from the title (about 120 words)."""
    topic = title.rstrip(".!?")
    return [
        f"Today on {channel_name} we look at {topic}.",
        "It starts quietly, the way most of these stories do, with one ordinary decision.",
        "Nobody around noticed at first, and that is exactly why it matters.",
        f"The part people remember about {topic} is not the ending but the moment before it.",
        "We walk through what happened, what it cost, and what changed afterwards.",
        "Along the way there are three small details that most retellings leave out.",
        "The first is about timing, the second is about who was watching, "
        "and the third is about what was said next.",
        "By the end you will see why this story spread so far and so fast.",
        "Stay with us, because the last minute changes how the whole thing reads.",
        f"This has been {channel_name}; thank you for watching.",
    ]


# A valid 1x1 grey JPEG, used only when the fixture image is missing.
MINIMAL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b"
    "0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432"
    "ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1"
    "082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a5354"
    "55565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6"
    "a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4"
    "f5f6f7f8f9faffda0008010100003f00fbd0ffd9"
)


def write_fixture_json(path: Path, fixture: FixtureChannel) -> None:
    """Used by the fixture generator; keeps the files readable and stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
