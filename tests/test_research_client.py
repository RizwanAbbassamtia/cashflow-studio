"""yt-dlp client (research/ytdlp_client.py) against recorded fixtures: no network.

A fake ``YoutubeDL`` returns the recorded info dicts and caption bodies and records the
options it was built with, so the tests check both the mapping and that each call is the
Python form of the yt-dlp command in the module docstring.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from cashflow_studio.models.research import TranscriptDoc, TranscriptSegment
from cashflow_studio.research.config import ResearchConfig
from cashflow_studio.research.errors import (
    ResearchBlocked,
    ResearchError,
    TranscriptUnavailable,
    VideoNotFound,
)
from cashflow_studio.research.ytdlp_client import (
    YtDlpClient,
    choose_caption_track,
    looks_blocked,
    parse_json3,
    plain_error,
    spread_words,
    tab_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "research" / "ytdlp"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
CHANNEL = "https://www.youtube.com/@humanember"
VIDEO = "aB3dE5fG7hI"
MANUAL_URL = "https://www.youtube.com/api/timedtext?v=aB3dE5fG7hI&lang=en&fmt=json3"
AUTO_URL = "https://www.youtube.com/api/timedtext?v=aB3dE5fG7hI&kind=asr&lang=en&fmt=json3"
ORIG_URL = "https://www.youtube.com/api/timedtext?v=aB3dE5fG7hI&kind=asr&lang=en-orig&fmt=json3"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeDownloadError(Exception):
    """Stands in for ``yt_dlp.utils.DownloadError`` (any exception type is handled)."""


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class FakeYDL:
    def __init__(
        self,
        params: dict[str, Any],
        responses: dict[str, Any],
        failures: list[Exception],
        bodies: dict[str, bytes],
        urls: list[str],
    ) -> None:
        self.params = params
        self.responses = responses
        self.failures = failures
        self.bodies = bodies
        self.urls = urls
        self.closed = False

    def extract_info(self, url: str, download: bool = True) -> Any:
        assert download is False
        self.urls.append(url)
        if self.failures:
            raise self.failures.pop(0)
        for key, info in self.responses.items():
            if url == key or url.endswith(key):
                return info
        raise AssertionError(f"unexpected yt-dlp url {url}")

    def sanitize_info(self, info: Any) -> Any:
        return dict(info)

    def urlopen(self, url: str) -> _Response:
        return _Response(self.bodies[url])

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        failures: list[Exception] | None = None,
        bodies: dict[str, bytes] | None = None,
        http: dict[str, tuple[int, bytes]] | None = None,
        config: ResearchConfig | None = None,
        transcript_fallback: Any = None,
    ) -> None:
        self.responses = responses or {}
        self.failures = failures or []
        self.bodies = bodies or {}
        self.http_answers = http or {}
        self.http_calls: list[tuple[str, str]] = []
        self.created: list[FakeYDL] = []
        self.urls: list[str] = []
        self.sleeps: list[float] = []
        self.waits: list[tuple[float, str]] = []
        self.client = YtDlpClient(
            config=config or ResearchConfig(backoff_minutes=[10, 20]),
            cache_dir=Path("C:/cfs-cache"),
            ydl_factory=self._factory,
            http=self._http,
            sleep=self.sleeps.append,
            now=lambda: NOW,
            transcript_fallback=transcript_fallback,
            on_wait=lambda seconds, reason: self.waits.append((seconds, reason)),
        )

    def _factory(self, params: dict[str, Any]) -> FakeYDL:
        ydl = FakeYDL(params, self.responses, self.failures, self.bodies, self.urls)
        self.created.append(ydl)
        return ydl

    def _http(self, method: str, url: str) -> tuple[int, bytes]:
        self.http_calls.append((method, url))
        return self.http_answers.get(url, (404, b""))

    @property
    def last_params(self) -> dict[str, Any]:
        return self.created[-1].params


# list_tab -------------------------------------------------------------------------------------


def test_list_tab_uses_the_flat_listing_options_of_the_cli() -> None:
    h = Harness(responses={"/videos": load("flat_videos_tab.json")})
    h.client.list_tab(CHANNEL, "videos", max_videos=50)
    params = h.last_params
    assert h.urls == [f"{CHANNEL}/videos"]
    assert params["extract_flat"] == "in_playlist"
    assert params["skip_download"] is True
    assert params["sleep_interval_requests"] == 0.75
    assert params["extractor_args"] == {"youtubetab": {"approximate_date": [""]}}
    assert params["playlistend"] == 50
    assert params["quiet"] is True and params["noprogress"] is True
    assert Path(params["cachedir"]) == Path("C:/cfs-cache")
    assert h.created[-1].closed is True


def test_list_tab_maps_flat_entries_in_tab_order() -> None:
    h = Harness(responses={"/videos": load("flat_videos_tab.json")})
    listing = h.client.list_tab(CHANNEL, "videos")
    assert listing.channel_url == CHANNEL
    assert listing.channel_id == "UCFjva5hxOFoj2ViNgSuEQJg"
    assert listing.channel_name == "Human Ember"
    assert listing.channel_follower_count == 251000
    assert listing.scanned_at == NOW
    assert listing.from_cache is False
    assert [v.video_id for v in listing.videos] == [
        VIDEO, "bC4eF6gH8iJ", "cD5fG7hI9jK", "dE6gH8iJ0kL"
    ]
    assert [v.position for v in listing.videos] == [0, 1, 2, 3]

    first = listing.videos[0]
    assert first.title == "The stranger who paid for every meal in the diner"
    assert first.url == f"https://www.youtube.com/watch?v={VIDEO}"
    assert first.tab == "videos" and first.format == "long"
    assert first.duration_s == 612
    assert first.view_count == 520000
    assert first.published_at == datetime(2025, 9, 9, tzinfo=UTC)  # approximate_date timestamp
    # the largest of the listed thumbnails
    assert first.thumbnail_url == f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg?sqp=large"
    assert first.live_status is None
    assert first.channel_follower_count == 251000
    assert first.fetched_at == NOW

    live = listing.videos[2]
    assert live.live_status == "was_live" and live.duration_s == 7210
    premiere = listing.videos[3]
    assert premiere.live_status == "is_upcoming"
    assert premiere.duration_s is None and premiere.view_count is None
    assert premiere.published_at == datetime(2025, 9, 23, tzinfo=UTC)  # release_timestamp
    assert premiere.thumbnail_url == "https://i.ytimg.com/vi/dE6gH8iJ0kL/hqdefault.jpg"


def test_list_tab_shorts_marks_the_format_and_accepts_missing_durations() -> None:
    h = Harness(responses={"/shorts": load("flat_shorts_tab.json")})
    listing = h.client.list_tab(f"{CHANNEL}/videos", "shorts")  # a /videos link still works
    assert h.urls == [f"{CHANNEL}/shorts"]
    assert [v.format for v in listing.videos] == ["shorts", "shorts"]
    assert listing.videos[0].duration_s is None
    assert listing.videos[1].duration_s == 48
    assert listing.videos[0].url == "https://www.youtube.com/shorts/sH0rT000001"


def test_tab_url_strips_an_existing_tab() -> None:
    assert tab_url("https://www.youtube.com/@x/", "videos") == "https://www.youtube.com/@x/videos"
    assert tab_url("https://www.youtube.com/@x/videos", "shorts") == "https://www.youtube.com/@x/shorts"
    assert tab_url("https://www.youtube.com/channel/UCabc", "shorts") == (
        "https://www.youtube.com/channel/UCabc/shorts"
    )


# video_details --------------------------------------------------------------------------------


def test_video_details_uses_the_player_skip_options_and_maps_exact_numbers() -> None:
    h = Harness(responses={f"watch?v={VIDEO}": load("video_details.json")})
    details = h.client.video_details(VIDEO)
    params = h.last_params
    assert h.urls == [f"https://www.youtube.com/watch?v={VIDEO}"]
    assert params["extractor_args"] == {"youtube": {"player_skip": ["js"], "skip": ["hls", "dash"]}}
    assert params["skip_download"] is True
    assert "extract_flat" not in params

    assert details.video_id == VIDEO
    assert details.view_count == 523418
    assert details.like_count == 21877
    assert details.comment_count == 1312
    assert details.upload_date == date(2025, 9, 9)
    assert details.published_at == datetime(2025, 9, 9, 7, tzinfo=UTC)
    assert details.duration_s == 612
    assert details.tags == ["kindness", "true story", "diner"]
    assert details.categories == ["People & Blogs"]
    assert details.description.startswith("In 1994 a man walked into a diner")
    assert [(c.title, c.start_s, c.end_s) for c in details.chapters] == [
        ("The diner", 0.0, 45.0),
        ("The stranger", 45.0, 612.0),
    ]
    assert details.caption_languages.manual == ["en"]
    assert details.caption_languages.auto == ["en", "en-orig", "es"]
    assert details.thumbnail_url == f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg"
    assert details.channel_follower_count == 251000
    assert details.language == "en"
    assert details.fetched_at == NOW


def test_video_details_reports_a_missing_video_plainly() -> None:
    h = Harness(failures=[FakeDownloadError("ERROR: [youtube] zzz: Video unavailable")])
    with pytest.raises(VideoNotFound, match="not available"):
        h.client.video_details("zzz")
    assert h.sleeps == []


# transcript -----------------------------------------------------------------------------------


def test_transcript_prefers_manual_captions_and_uses_the_json3_track() -> None:
    bodies = {MANUAL_URL: (FIXTURES / "captions_manual.json3").read_bytes()}
    h = Harness(responses={f"watch?v={VIDEO}": load("video_details.json")}, bodies=bodies)
    doc = h.client.transcript(VIDEO, "en")
    params = h.last_params
    assert params["writesubtitles"] is True and params["writeautomaticsub"] is True
    assert params["subtitleslangs"] == ["en.*"]
    assert params["subtitlesformat"] == "json3"

    assert doc.source == "yt-dlp" and doc.kind == "manual" and doc.language == "en"
    assert [s.text for s in doc.segments] == ["Hello there friends", "welcome back"]
    assert [(s.start, s.end) for s in doc.segments] == [(1.0, 3.0), (3.0, 4.5)]
    # Manual lines carry no word offsets: words are spread evenly over the line.
    assert [w.text for w in doc.words] == ["Hello", "there", "friends", "welcome", "back"]
    assert [w.start for w in doc.words] == pytest.approx([1.0, 1.667, 2.333, 3.0, 3.75], abs=1e-3)
    assert doc.words[2].end == 3.0
    assert doc.text == "Hello there friends welcome back"


def test_transcript_uses_auto_captions_with_word_offsets_when_no_manual_track() -> None:
    info = load("video_details.json")
    del info["subtitles"]
    # No exact en-US track: the original-language auto track (en-orig) wins over plain en.
    bodies = {ORIG_URL: (FIXTURES / "captions_auto.json3").read_bytes()}
    h = Harness(responses={f"watch?v={VIDEO}": info}, bodies=bodies)
    doc = h.client.transcript(VIDEO, "en-US")
    assert doc.kind == "auto" and doc.language == "en-orig"
    assert [s.text for s in doc.segments] == ["She found the wallet", "on a Tuesday"]
    assert [(s.start, s.end) for s in doc.segments] == [(0.12, 3.52), (3.52, 6.42)]
    words = [(w.text, w.start, w.end) for w in doc.words]
    assert words == [
        ("She", 0.12, 0.52),
        ("found", 0.52, 0.92),
        ("the", 0.92, 1.12),
        ("wallet", 1.12, 3.52),
        ("on", 3.52, 3.82),
        ("a", 3.82, 4.02),
        ("Tuesday", 4.02, 6.42),
    ]


def test_transcript_falls_back_to_youtube_transcript_api_when_yt_dlp_has_no_track() -> None:
    info = load("video_details.json")
    info["subtitles"] = {}
    info["automatic_captions"] = {"fr": info["automatic_captions"]["es"]}
    asked: list[tuple[str, str]] = []

    def fallback(video_id: str, lang: str) -> TranscriptDoc:
        asked.append((video_id, lang))
        return TranscriptDoc(
            video_id=video_id,
            language="en",
            source="youtube-transcript-api",
            segments=[TranscriptSegment(start=0, end=2, text="from the fallback")],
            text="from the fallback",
        )

    h = Harness(responses={f"watch?v={VIDEO}": info}, transcript_fallback=fallback)
    doc = h.client.transcript(VIDEO, "en")
    assert asked == [(VIDEO, "en")]
    assert doc.source == "youtube-transcript-api"
    assert doc.segments[0].text == "from the fallback"


def test_transcript_unavailable_when_nothing_has_captions() -> None:
    info = load("video_details.json")
    info["subtitles"] = {}
    info["automatic_captions"] = {}
    h = Harness(responses={f"watch?v={VIDEO}": info}, transcript_fallback=lambda v, lang: None)
    with pytest.raises(TranscriptUnavailable, match="no captions"):
        h.client.transcript(VIDEO, "en")


def test_choose_caption_track_prefers_exact_then_orig_then_regional() -> None:
    info = load("video_details.json")
    assert choose_caption_track(info, "en") == ("en", "manual", MANUAL_URL)
    del info["subtitles"]
    assert choose_caption_track(info, "en") == ("en", "auto", AUTO_URL)
    del info["automatic_captions"]["en"]
    assert choose_caption_track(info, "en") == ("en-orig", "auto", ORIG_URL)
    info["automatic_captions"] = {"en-GB": info["automatic_captions"]["es"]}
    regional_url = info["automatic_captions"]["en-GB"][0]["url"]
    assert choose_caption_track(info, "en") == ("en-GB", "auto", regional_url)
    assert choose_caption_track(info, "de") is None
    assert choose_caption_track({}, "en") is None


def test_parse_json3_by_hand() -> None:
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100},  # window definition, no text
            {
                "tStartMs": 500,
                "dDurationMs": 2000,
                "segs": [{"utf8": "two"}, {"utf8": " words", "tOffsetMs": 1000}],
            },
            {"tStartMs": 2500, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "single line here"}]},
        ]
    }
    segments, words = parse_json3(data)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.5, 2.5, "two words"),
        (3.0, 4.0, "single line here"),
    ]
    assert [(w.text, w.start, w.end) for w in words] == [
        ("two", 0.5, 1.5),
        ("words", 1.5, 2.5),
        ("single", 3.0, pytest.approx(3.333, abs=1e-3)),
        ("line", pytest.approx(3.333, abs=1e-3), pytest.approx(3.667, abs=1e-3)),
        ("here", pytest.approx(3.667, abs=1e-3), 4.0),
    ]
    assert parse_json3({}) == ([], [])
    assert [w.text for w in spread_words("  a  b ", 0, 1)] == ["a", "b"]


# thumbnail ------------------------------------------------------------------------------------


def test_thumbnail_tries_maxres_then_sd_then_hq_with_head_requests(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff\xe0JFIF-bytes"
    hq = f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"
    h = Harness(http={hq: (200, jpeg)})
    dest = tmp_path / "thumb" / "competitor_thumbnail.jpg"
    saved = h.client.thumbnail(VIDEO, dest)
    assert saved == dest and dest.read_bytes() == jpeg
    assert h.http_calls == [
        ("HEAD", f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg"),
        ("HEAD", f"https://i.ytimg.com/vi/{VIDEO}/sddefault.jpg"),
        ("HEAD", hq),
        ("GET", hq),
    ]


def test_thumbnail_skips_non_jpeg_answers_and_fails_plainly(tmp_path: Path) -> None:
    maxres = f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg"
    h = Harness(http={maxres: (200, b"<html>not an image</html>")})
    with pytest.raises(ResearchError, match="No thumbnail could be downloaded"):
        h.client.thumbnail(VIDEO, tmp_path / "t.jpg")
    assert not (tmp_path / "t.jpg").exists()


# back-off -------------------------------------------------------------------------------------


def test_backoff_waits_ten_then_twenty_minutes_then_raises_research_blocked() -> None:
    blocked = FakeDownloadError(
        "ERROR: [youtube] aB3dE5fG7hI: Sign in to confirm you're not a bot. Use --cookies ..."
    )
    h = Harness(failures=[blocked, blocked, blocked])
    with pytest.raises(ResearchBlocked) as info:
        h.client.list_tab(CHANNEL, "videos")
    assert h.sleeps == [600.0, 1200.0]
    assert [w[0] for w in h.waits] == [600.0, 1200.0]
    assert "slow down or sign in" in str(info.value)
    assert "--cookies" not in str(info.value)
    assert len(h.urls) == 3  # three attempts, never an endless loop


def test_backoff_recovers_when_youtube_answers_after_a_wait() -> None:
    h = Harness(
        responses={"/videos": load("flat_videos_tab.json")},
        failures=[FakeDownloadError("HTTP Error 429: Too Many Requests")],
    )
    listing = h.client.list_tab(CHANNEL, "videos")
    assert len(listing.videos) == 4
    assert h.sleeps == [600.0]


def test_rate_limit_status_codes_and_messages_count_as_blocked() -> None:
    class Status(Exception):
        status = 429

    assert looks_blocked(Status("x")) is True
    assert looks_blocked(FakeDownloadError("Please try again later")) is True
    assert looks_blocked(FakeDownloadError("Video unavailable")) is False


def test_other_errors_become_plain_research_errors_without_waiting() -> None:
    no_tab = FakeDownloadError("ERROR: [youtube:tab] @nobody: Unable to recognize tab page")
    h = Harness(failures=[no_tab])
    with pytest.raises(VideoNotFound, match="Unable to recognize tab page"):
        h.client.list_tab("https://www.youtube.com/@nobody", "videos")
    other = FakeDownloadError(
        "ERROR: [youtube] abc: The uploader has not made this video available"
    )
    h = Harness(failures=[other])
    with pytest.raises(ResearchError) as info:
        h.client.video_details("abc")
    assert str(info.value) == (
        "YouTube did not answer while reading video abc: "
        "The uploader has not made this video available"
    )
    assert h.sleeps == []
    assert plain_error("ERROR: [youtube] abc: Private video\nmore lines") == "Private video"
