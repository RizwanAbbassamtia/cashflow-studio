"""YouTube data through the yt-dlp Python API (pinned ``yt-dlp[default]``), no API key.

Every method is the Python form of one yt-dlp command:

``list_tab(channel_url, tab)`` ::

    yt-dlp --flat-playlist --skip-download --sleep-requests 0.75 \\
           --extractor-args "youtubetab:approximate_date" --playlist-end 200 \\
           -J "https://www.youtube.com/@handle/videos"      (or /shorts)

  ``extract_flat='in_playlist'`` lists the tab without opening each video, so the view counts
  are the rounded numbers shown on the tab and the dates are approximate ("3 weeks ago").

``video_details(video_id)`` ::

    yt-dlp --skip-download --extractor-args "youtube:player_skip=js;skip=hls,dash" \\
           -J "https://www.youtube.com/watch?v=VIDEO_ID"

  Exact view, like and comment counts, upload date, tags, description, chapters, the
  caption languages and the best thumbnail URL. Skipping the JS player and the HLS/DASH
  manifests keeps it to a couple of requests.

``transcript(video_id, lang)`` ::

    yt-dlp --skip-download --write-subs --write-auto-subs --sub-langs "en.*" \\
           --sub-format json3 "https://www.youtube.com/watch?v=VIDEO_ID"

  The json3 track carries one event per caption line and one segment per word with its
  offset, which gives word timings. Manual captions win over auto captions. When yt-dlp
  lists no track, ``youtube-transcript-api`` is tried (segments only; words are spread
  evenly across each segment).

``thumbnail(video_id, dest)`` ::

    curl -I https://i.ytimg.com/vi/VIDEO_ID/maxresdefault.jpg   (then sddefault, hqdefault)

  The first size that answers 200 is downloaded and saved as JPEG.

Back-off: when YouTube answers "Sign in to confirm you're not a bot", "try again later" or
HTTP 429, the client waits 10 minutes, then 20, 40 and 60 (``backoff_minutes`` in
``config/research.yaml``), retrying after each wait; after the last wait it raises
:class:`ResearchBlocked` with a plain-English message. It never loops forever.

Caching (``research_videos`` / ``research_scans`` in SQLite) is done by ``scanner.py`` around
this client, so the mock provider's results are cached the same way.

Nothing here touches API keys. Tests inject a fake ``YoutubeDL`` factory, an HTTP function
and a ``sleep`` so no test reaches the network.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..models.research import (
    CaptionLanguages,
    Chapter,
    FlatVideo,
    Tab,
    TabListing,
    TranscriptDoc,
    TranscriptSegment,
    TranscriptWord,
    VideoDetails,
    format_for_tab,
)
from .config import ResearchConfig
from .errors import ResearchBlocked, ResearchError, TranscriptUnavailable, VideoNotFound
from .languages import caption_track_candidates

log = logging.getLogger(__name__)

YdlFactory = Callable[[dict[str, Any]], Any]
HttpFn = Callable[[str, str], tuple[int, bytes]]
"""``http(method, url) -> (status, body)``; ``body`` is empty for HEAD."""
TranscriptFallbackFn = Callable[[str, str], TranscriptDoc | None]
"""``fallback(video_id, lang)`` -> a transcript or ``None`` when there is none."""
WaitFn = Callable[[float, str], None]
"""``on_wait(seconds, reason)`` is called before each back-off sleep (for progress text)."""

WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/{name}.jpg"
THUMBNAIL_SIZES: tuple[str, ...] = ("maxresdefault", "sddefault", "hqdefault")
JPEG_MAGIC = b"\xff\xd8"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)

BLOCKED_RE = re.compile(
    r"sign in to confirm|try again later|\b429\b|too many requests|rate[- ]?limit",
    re.IGNORECASE,
)
NOT_FOUND_RE = re.compile(
    r"video unavailable|private video|has been removed|is not available|"
    r"incomplete youtube id|does not exist|this channel does not have|"
    r"unable to recognize tab page|is not a valid url|no video formats found|"
    r"members-only|this video has been removed",
    re.IGNORECASE,
)
# "ERROR: [youtube] VIDEO_ID: message" -> "message" (the id is any single token before a colon).
PREFIX_RE = re.compile(r"^(?:ERROR:\s*)?(?:\[[^\]]+\]\s*)+(?:[A-Za-z0-9_@.-]{1,64}:\s+)?")

BLOCKED_MESSAGE = (
    "YouTube is asking this computer to slow down or sign in, so research is paused for now. "
    "Wait about an hour and scan again, or try from another internet connection."
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class _YtDlpLogger:
    """Keeps yt-dlp's chatter in the debug log; errors go to the warning log."""

    def debug(self, message: str) -> None:
        if not message.startswith("[debug] "):
            log.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        log.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        log.debug("yt-dlp warning: %s", message)

    def error(self, message: str) -> None:
        log.warning("yt-dlp: %s", message)


def _real_ydl_factory(options: dict[str, Any]) -> Any:
    from yt_dlp import YoutubeDL

    return YoutubeDL(options)


def urllib_http(method: str, url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    """Default HTTP function for thumbnails: standard library only."""
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            body = response.read() if method.upper() == "GET" else b""
            return status, body
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise ResearchError(f"The thumbnail could not be downloaded: {reason}") from exc


def plain_error(message: str) -> str:
    """Strip yt-dlp's ``ERROR: [youtube] id:`` prefixes and keep the first sentence or two."""
    text = PREFIX_RE.sub("", (message or "").strip())
    text = text.split("\n", 1)[0].strip()
    return text or "YouTube did not answer."


def looks_blocked(exc: BaseException) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    if status == 429:
        return True
    return bool(BLOCKED_RE.search(str(exc)))


def looks_not_found(message: str) -> bool:
    return bool(NOT_FOUND_RE.search(message))


class YtDlpClient:
    """The yt-dlp research provider. ``YtDlpProvider`` is an alias."""

    name = "ytdlp"

    def __init__(
        self,
        *,
        config: ResearchConfig | None = None,
        cache_dir: Path | None = None,
        ydl_factory: YdlFactory | None = None,
        http: HttpFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = utc_now,
        transcript_fallback: TranscriptFallbackFn | None = None,
        on_wait: WaitFn | None = None,
    ) -> None:
        self.config = config or ResearchConfig()
        self.cache_dir = cache_dir
        self._ydl_factory = ydl_factory or _real_ydl_factory
        self._http = http or self._default_http
        self._sleep = sleep
        self._now = now
        self._transcript_fallback = transcript_fallback
        self.on_wait = on_wait
        self.backoff_seconds: list[float] | None = None
        """Overrides ``config.backoff_seconds`` when set; ``[]`` means "never wait, fail at
        once" (used while a reviewer's request is waiting for the answer)."""
        self.calls: list[tuple[str, str]] = []
        """``(method, url)`` of every yt-dlp call, for tests and the log."""

    # Options -------------------------------------------------------------------------------

    def base_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "logger": _YtDlpLogger(),
            "socket_timeout": self.config.http_timeout_seconds,
            "retries": 2,
            "extractor_retries": 1,
            "ignoreerrors": False,
        }
        if self.cache_dir is not None:
            options["cachedir"] = str(self.cache_dir)
        return options

    def list_tab_options(self, max_videos: int) -> dict[str, Any]:
        return self.base_options() | {
            "extract_flat": "in_playlist",
            "sleep_interval_requests": self.config.sleep_interval_requests,
            "extractor_args": {"youtubetab": {"approximate_date": [""]}},
            "playlistend": max_videos,
        }

    def video_options(self) -> dict[str, Any]:
        return self.base_options() | {
            "extractor_args": {"youtube": {"player_skip": ["js"], "skip": ["hls", "dash"]}},
        }

    def transcript_options(self, lang: str) -> dict[str, Any]:
        base = lang.split("-")[0].lower()
        return self.video_options() | {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [f"{base}.*"],
            "subtitlesformat": "json3",
        }

    # Public API ----------------------------------------------------------------------------

    def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
        url = tab_url(channel_url, tab)
        info = self._extract(url, self.list_tab_options(max_videos), what=f"listing {url}")
        if not isinstance(info, dict):
            raise ResearchError(f"YouTube returned nothing for {url}.")
        scanned_at = self._now()
        entries = info.get("entries") or []
        videos: list[FlatVideo] = []
        for position, entry in enumerate(_iter_entries(entries)):
            video = flat_video_from_entry(
                entry,
                tab=tab,
                position=position,
                channel_url=channel_url,
                channel_id=info.get("channel_id"),
                channel_name=info.get("channel") or info.get("uploader") or "",
                followers=_int(info.get("channel_follower_count")),
                fetched_at=scanned_at,
            )
            if video is not None:
                videos.append(video)
        return TabListing(
            channel_url=channel_url,
            tab=tab,
            channel_id=info.get("channel_id"),
            channel_name=info.get("channel") or info.get("uploader") or "",
            channel_follower_count=_int(info.get("channel_follower_count")),
            scanned_at=scanned_at,
            videos=videos,
        )

    def video_details(self, video_id: str) -> VideoDetails:
        url = WATCH_URL.format(video_id=video_id)
        info = self._extract(url, self.video_options(), what=f"reading video {video_id}")
        if not isinstance(info, dict):
            raise VideoNotFound(f"The video {video_id} could not be read from YouTube.")
        return video_details_from_info(info, video_id=video_id, fetched_at=self._now())

    def transcript(self, video_id: str, lang: str) -> TranscriptDoc:
        url = WATCH_URL.format(video_id=video_id)
        options = self.transcript_options(lang)
        ydl = self._ydl_factory(options)
        try:
            info = self._with_backoff(
                lambda: ydl.extract_info(url, download=False),
                what=f"reading captions of {video_id}",
            )
            self.calls.append(("transcript", url))
            track = choose_caption_track(info if isinstance(info, dict) else {}, lang)
            if track is not None:
                track_lang, kind, track_url = track
                raw = self._with_backoff(
                    lambda: ydl.urlopen(track_url).read(),
                    what=f"downloading captions of {video_id}",
                )
                data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                segments, words = parse_json3(data)
                if segments:
                    return TranscriptDoc(
                        video_id=video_id,
                        language=track_lang,
                        source="yt-dlp",
                        kind=kind,
                        segments=segments,
                        words=words,
                        text=" ".join(s.text for s in segments),
                    )
        finally:
            _close(ydl)
        fallback = self._transcript_fallback or self._default_fallback
        doc = fallback(video_id, lang)
        if doc is None or not doc.segments:
            raise TranscriptUnavailable(
                "This video has no captions in a language we can use, so there is no "
                "transcript to work from."
            )
        return doc

    def thumbnail(self, video_id: str, dest: Path, *, fallback_url: str | None = None) -> Path:
        dest = Path(dest)
        urls = [THUMBNAIL_URL.format(video_id=video_id, name=name) for name in THUMBNAIL_SIZES]
        if fallback_url:
            urls.append(fallback_url)
        for url in urls:
            status, _ = self._http("HEAD", url)
            if status != 200:
                continue
            status, body = self._http("GET", url)
            if status != 200 or not body or not body.startswith(JPEG_MAGIC):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return dest
        raise ResearchError("No thumbnail could be downloaded for this video.")

    # Internals -----------------------------------------------------------------------------

    def _extract(self, url: str, options: dict[str, Any], *, what: str) -> Any:
        ydl = self._ydl_factory(options)
        try:
            info = self._with_backoff(lambda: ydl.extract_info(url, download=False), what=what)
            self.calls.append(("extract_info", url))
            sanitize = getattr(ydl, "sanitize_info", None)
            return sanitize(info) if callable(sanitize) and isinstance(info, dict) else info
        finally:
            _close(ydl)

    def _with_backoff(self, action: Callable[[], Any], *, what: str) -> Any:
        delays = self.config.backoff_seconds
        if self.backoff_seconds is not None:
            delays = self.backoff_seconds
        attempt = 0
        while True:
            try:
                return action()
            except ResearchError:
                raise
            except Exception as exc:
                if not looks_blocked(exc):
                    message = plain_error(str(exc))
                    if looks_not_found(message):
                        raise VideoNotFound(
                            f"YouTube says this video or channel is not available ({message})."
                        ) from exc
                    raise ResearchError(f"YouTube did not answer while {what}: {message}") from exc
                if attempt >= len(delays):
                    log.warning("YouTube blocked %s after %d waits", what, attempt)
                    raise ResearchBlocked(BLOCKED_MESSAGE) from exc
                wait = delays[attempt]
                attempt += 1
                log.info("YouTube asked us to slow down while %s; waiting %.0f s", what, wait)
                if self.on_wait is not None:
                    self.on_wait(wait, plain_error(str(exc)))
                self._sleep(wait)

    def _default_http(self, method: str, url: str) -> tuple[int, bytes]:
        return urllib_http(method, url, self.config.http_timeout_seconds)

    def _default_fallback(self, video_id: str, lang: str) -> TranscriptDoc | None:
        return youtube_transcript_api_fallback(
            video_id, lang, self.config.transcript_fallback_languages
        )


YtDlpProvider = YtDlpClient


# Mapping helpers (pure) ----------------------------------------------------------------------


def tab_url(channel_url: str, tab: Tab) -> str:
    base = channel_url.strip().rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/featured"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return f"{base}/{tab}"


def _iter_entries(entries: Any) -> list[dict[str, Any]]:
    return [entry for entry in entries if isinstance(entry, dict)]


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    number = _int(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=UTC)


def _upload_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def best_thumbnail_url(info: dict[str, Any]) -> str | None:
    """The largest thumbnail yt-dlp lists, else the single ``thumbnail`` field."""
    thumbnails = info.get("thumbnails")
    best: tuple[int, str] | None = None
    if isinstance(thumbnails, list):
        for item in thumbnails:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            size = (_int(item.get("width")) or 0) * (_int(item.get("height")) or 0)
            if best is None or size > best[0]:
                best = (size, str(item["url"]))
    if best is not None:
        return best[1]
    url = info.get("thumbnail")
    return str(url) if url else None


def flat_video_from_entry(
    entry: dict[str, Any],
    *,
    tab: Tab,
    position: int,
    channel_url: str,
    channel_id: str | None,
    channel_name: str,
    followers: int | None,
    fetched_at: datetime,
) -> FlatVideo | None:
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not video_id:
        return None
    return FlatVideo(
        video_id=video_id,
        url=entry.get("url") or WATCH_URL.format(video_id=video_id),
        title=str(entry.get("title") or ""),
        channel_url=channel_url,
        channel_id=entry.get("channel_id") or channel_id,
        channel_name=entry.get("channel") or entry.get("uploader") or channel_name,
        tab=tab,
        format=format_for_tab(tab),
        position=position,
        duration_s=_int(entry.get("duration")),
        view_count=_int(entry.get("view_count")),
        published_at=_timestamp(entry.get("timestamp") or entry.get("release_timestamp")),
        thumbnail_url=best_thumbnail_url(entry),
        live_status=entry.get("live_status"),
        channel_follower_count=_int(entry.get("channel_follower_count")) or followers,
        fetched_at=fetched_at,
    )


def video_details_from_info(
    info: dict[str, Any], *, video_id: str, fetched_at: datetime
) -> VideoDetails:
    chapters: list[Chapter] = []
    for raw in info.get("chapters") or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("start_time")
        if start is None:
            continue
        chapters.append(
            Chapter(
                title=str(raw.get("title") or ""),
                start_s=float(start),
                end_s=float(raw["end_time"]) if raw.get("end_time") is not None else None,
            )
        )
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    return VideoDetails(
        video_id=str(info.get("id") or video_id),
        url=str(info.get("webpage_url") or WATCH_URL.format(video_id=video_id)),
        title=str(info.get("title") or ""),
        channel_id=info.get("channel_id"),
        channel_name=str(info.get("channel") or info.get("uploader") or ""),
        channel_url=info.get("channel_url") or info.get("uploader_url"),
        channel_follower_count=_int(info.get("channel_follower_count")),
        duration_s=_int(info.get("duration")),
        view_count=_int(info.get("view_count")),
        like_count=_int(info.get("like_count")),
        comment_count=_int(info.get("comment_count")),
        upload_date=_upload_date(info.get("upload_date")),
        published_at=_timestamp(info.get("timestamp") or info.get("release_timestamp")),
        tags=[str(tag) for tag in info.get("tags") or []],
        categories=[str(item) for item in info.get("categories") or []],
        description=str(info.get("description") or ""),
        chapters=chapters,
        caption_languages=CaptionLanguages(
            manual=sorted(str(key) for key in subtitles),
            auto=sorted(str(key) for key in automatic),
        ),
        thumbnail_url=best_thumbnail_url(info),
        live_status=info.get("live_status"),
        language=info.get("language"),
        fetched_at=fetched_at,
    )


def choose_caption_track(info: dict[str, Any], lang: str) -> tuple[str, str, str] | None:
    """``(language, "manual"|"auto", json3 url)`` for the best track of ``lang``, or ``None``.

    Manual captions win over auto captions; an exact code wins over ``xx-orig`` and regional
    variants (``en-US``).
    """
    base = lang.split("-")[0].lower()
    wanted = caption_track_candidates(lang)
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        tracks = info.get(key) or {}
        if not isinstance(tracks, dict):
            continue
        ordered = [name for name in wanted if name in tracks]
        ordered += sorted(
            name
            for name in tracks
            if name not in ordered and str(name).lower().split("-")[0] == base
        )
        for name in ordered:
            url = _json3_url(tracks.get(name))
            if url:
                return str(name), kind, url
    return None


def _json3_url(formats: Any) -> str | None:
    if not isinstance(formats, list):
        return None
    for item in formats:
        if isinstance(item, dict) and item.get("ext") == "json3" and item.get("url"):
            return str(item["url"])
    return None


def parse_json3(data: dict[str, Any]) -> tuple[list[TranscriptSegment], list[TranscriptWord]]:
    """Caption lines and word timings from YouTube's json3 format.

    Each event is one caption line (``tStartMs``, ``dDurationMs``) with ``segs``; auto
    captions carry one segment per word with ``tOffsetMs``, manual captions one segment per
    line (words are then spread evenly over the line).
    """
    segments: list[TranscriptSegment] = []
    words: list[TranscriptWord] = []
    events = [e for e in (data.get("events") or []) if isinstance(e, dict) and e.get("segs")]
    for index, event in enumerate(events):
        start_ms = _int(event.get("tStartMs")) or 0
        duration_ms = _int(event.get("dDurationMs")) or 0
        parts = [
            (_int(seg.get("tOffsetMs")) or 0, str(seg.get("utf8") or ""))
            for seg in event["segs"]
            if isinstance(seg, dict)
        ]
        text = " ".join("".join(t for _, t in parts).split())
        if not text:
            continue
        start = start_ms / 1000.0
        end = (start_ms + duration_ms) / 1000.0 if duration_ms else start
        # Auto captions overlap the next line; clamp to the next event's start.
        next_start = _next_event_start(events, index)
        if next_start is not None and next_start > start:
            end = min(end, next_start) if duration_ms else next_start
        if end <= start:
            end = start + max(0.5, 0.3 * len(text.split()))
        segments.append(TranscriptSegment(start=round(start, 3), end=round(end, 3), text=text))
        timed = [(offset, " ".join(t.split())) for offset, t in parts if t.strip()]
        if len(timed) > 1:
            for i, (offset, chunk) in enumerate(timed):
                chunk_start = (start_ms + offset) / 1000.0
                chunk_end = (start_ms + timed[i + 1][0]) / 1000.0 if i + 1 < len(timed) else end
                words.extend(spread_words(chunk, chunk_start, max(chunk_end, chunk_start)))
        else:
            words.extend(spread_words(text, start, end))
    return segments, words


def _next_event_start(events: list[dict[str, Any]], index: int) -> float | None:
    for later in events[index + 1 :]:
        segs = [seg for seg in later["segs"] if isinstance(seg, dict)]
        if any(str(seg.get("utf8") or "").strip() for seg in segs):
            return (_int(later.get("tStartMs")) or 0) / 1000.0
    return None


def spread_words(text: str, start: float, end: float) -> list[TranscriptWord]:
    """Give each word of ``text`` an equal share of ``[start, end]``."""
    tokens = text.split()
    if not tokens:
        return []
    span = max(end - start, 0.0)
    step = span / len(tokens)
    return [
        TranscriptWord(
            start=round(start + i * step, 3),
            end=round(start + (i + 1) * step if step else start, 3),
            text=token,
        )
        for i, token in enumerate(tokens)
    ]


def youtube_transcript_api_fallback(
    video_id: str, lang: str, extra_languages: list[str] | None = None
) -> TranscriptDoc | None:
    """Segments from ``youtube-transcript-api`` (no word offsets; words are spread evenly)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:  # pragma: no cover - declared dependency
        return None
    languages = list(dict.fromkeys([lang, lang.split("-")[0], *(extra_languages or [])]))
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    except Exception as exc:
        if exc.__class__.__name__ in {"IpBlocked", "RequestBlocked"}:
            raise ResearchBlocked(BLOCKED_MESSAGE) from exc
        log.info("youtube-transcript-api has no transcript for %s: %s", video_id, exc)
        return None
    segments = [
        TranscriptSegment(
            start=round(float(s.start), 3),
            end=round(float(s.start) + float(s.duration), 3),
            text=" ".join(str(s.text).split()),
        )
        for s in fetched
        if str(s.text).strip()
    ]
    words: list[TranscriptWord] = []
    for segment in segments:
        words.extend(spread_words(segment.text, segment.start, segment.end))
    return TranscriptDoc(
        video_id=video_id,
        language=getattr(fetched, "language_code", lang),
        source="youtube-transcript-api",
        kind="auto" if getattr(fetched, "is_generated", False) else "manual",
        segments=segments,
        words=words,
        text=" ".join(s.text for s in segments),
    )


def _close(ydl: Any) -> None:
    close = getattr(ydl, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - closing is best effort
            log.debug("yt-dlp close failed", exc_info=True)
