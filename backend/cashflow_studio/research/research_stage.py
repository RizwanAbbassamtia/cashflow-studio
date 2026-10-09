"""The research stage: scan the competitors, rank their videos, pick one, fetch its details.

Writes into ``01_research/`` (files are the truth, see docs/M1-M2-CONTRACT.md):

* ``candidates.json`` - every ranked candidate of the project's format, with exclusions
* ``pick.json``       - the one video production starts from (or the typed topic)
* ``video.json``      - exact details of the picked video
* ``transcript.json`` - its captions with word timings (``error`` set when there are none)
* ``competitor_thumbnail.jpg`` - its best thumbnail

Sources: ``ai_pick`` runs the picker; ``manual_pick`` uses the given video id (even one
outside the candidate list); ``own_topic`` skips scanning and only writes ``pick.json``.
``apply_edits`` (approve with ``{video_id}``) re-points the pick without a new scan and
records the person's choice in ``job.json`` (``source.kind = manual_pick``), so a later redo
keeps it. A redo of an AI pick lists the competitors afresh and proposes a video the AI has
not picked for this project before (the earlier ones are kept in ``rejected.json``).

The blocking provider calls run in a worker thread so the event loop keeps serving the UI.
The thread checks ``ctx.cancel`` between steps and before every write, so archiving the
project while research runs never recreates the folder.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models.project import ProjectSource, StageName
from ..models.research import (
    Candidate,
    ResearchFormat,
    ResearchPick,
    ResearchReviewPayload,
    TranscriptDoc,
    VideoDetails,
    tab_for_format,
)
from ..pipeline.stages.base import StageContext, StageError, StageResult
from ..storage.channel_store import ChannelStore
from .cache import ResearchCache
from .config import ResearchConfig, cached_research_config
from .errors import ResearchBlocked, ResearchError
from .languages import language_code
from .outliers import UsedHistory, age_days_of, label_for, views_per_day
from .picker import RerankFn, explain_pick, pick_one, rerank_enabled
from .provider import ResearchProvider, build_provider
from .scanner import (
    candidates_for,
    history_for,
    scan_competitors,
    write_back_competitor_stats,
)

log = logging.getLogger(__name__)

CANDIDATES_FILE = "candidates.json"
PICK_FILE = "pick.json"
VIDEO_FILE = "video.json"
TRANSCRIPT_FILE = "transcript.json"
THUMBNAIL_FILE = "competitor_thumbnail.jpg"
REJECTED_FILE = "rejected.json"
REVIEW_TOP_N = 50
CANCEL_WAIT_S = 30.0

CANCELLED_MESSAGE = "Stopped: the project was archived while research was running."


class ResearchCancelled(StageError):
    """The project was archived (or the app closed) while the worker thread ran."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class ResearchStage:
    name = StageName.research

    def __init__(
        self,
        *,
        provider: ResearchProvider | None = None,
        config: ResearchConfig | None = None,
        cache: ResearchCache | None = None,
        rerank: RerankFn | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.provider = provider
        self.config = config
        self.cache = cache
        self.rerank = rerank
        self._now = now

    # Stage protocol ------------------------------------------------------------------------

    async def run(self, ctx: StageContext) -> StageResult:
        research_dir = ctx.stage_dir(StageName.research)
        source = ctx.project.source
        if source.kind == "own_topic":
            return self._own_topic(ctx, research_dir)
        progress = self._progress_fn(ctx)
        manual_id = source.video_id if source.kind == "manual_pick" else None
        finished = threading.Event()

        def work() -> StageResult:
            try:
                return self._research(
                    ctx, research_dir, manual_video_id=manual_id, progress=progress
                )
            finally:
                finished.set()

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, work)
        try:
            return await future
        except asyncio.CancelledError:
            # The engine is archiving the project or shutting down: tell the thread to stop
            # and wait for it, so nothing is written into a folder that is being moved.
            ctx.cancel.set()
            try:
                await asyncio.shield(loop.run_in_executor(None, finished.wait, CANCEL_WAIT_S))
            except asyncio.CancelledError:
                pass
            raise

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approve with ``{video_id}``: make that video the pick without rescanning.

        Runs inside the approve request, so it never waits for a YouTube back-off: a "slow
        down" answer becomes a plain error the reviewer can act on. The person's choice is
        written into ``job.json`` as ``source = manual_pick`` so a redo keeps it.
        """
        video_id = str(ctx.edits.get("video_id") or "").strip()
        if not video_id:
            return None
        research_dir = ctx.stage_dir(StageName.research)
        progress = self._progress_fn(ctx)
        result = await asyncio.to_thread(
            self._research,
            ctx,
            research_dir,
            manual_video_id=video_id,
            progress=progress,
            chosen_by_person=True,
            wait_for_youtube=False,
        )
        ctx.project.source = ProjectSource(
            kind="manual_pick",
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
        )
        return result

    # Own topic -----------------------------------------------------------------------------

    def _own_topic(self, ctx: StageContext, research_dir: Path) -> StageResult:
        topic = (ctx.project.source.topic_text or "").strip()
        if not topic:
            raise StageError("Type the topic you want a video about before starting.")
        now = self._now()
        pick = ResearchPick(
            kind="own_topic",
            topic_text=topic,
            strategy="own_topic",
            reason="Topic typed by a person; the competitor scan was skipped.",
            picked_at=now,
        )
        pick_path = research_dir / PICK_FILE
        _write_json(pick_path, pick)
        if not ctx.project.title:
            ctx.project.title = topic
        payload = ResearchReviewPayload(
            source_kind="own_topic",
            topic_text=topic,
            format=ctx.project.format,
            notes=["No competitor video was scanned for this project."],
        )
        return StageResult(
            outputs=[pick_path],
            summary=f"Own topic: {topic}. No competitor video was scanned.",
            needs_review_payload=payload.as_payload(),
        )

    # Research ------------------------------------------------------------------------------

    def _research(
        self,
        ctx: StageContext,
        research_dir: Path,
        *,
        manual_video_id: str | None,
        progress: Callable[[str, float | None], None],
        chosen_by_person: bool = False,
        wait_for_youtube: bool = True,
    ) -> StageResult:
        config = self.config or cached_research_config()
        provider = _with_progress(
            self._provider_for(ctx, config), progress, wait_for_youtube=wait_for_youtube
        )
        cache = self.cache or ResearchCache(ctx.settings.app_data_dir)
        channel = ctx.channel
        project = ctx.project
        fmt: ResearchFormat = project.format
        now = self._now()
        notes: list[str] = []

        if not channel.competitors and manual_video_id is None:
            raise StageError(
                "This channel has no competitor channels yet. Add at least one in Channel "
                "Setup, or start from your own topic."
            )

        # A redo of an AI pick: list the competitors afresh and do not propose the videos
        # the AI already picked for this project (the reviewer asked for something else).
        previous = _read_pick(research_dir)
        redo = (
            manual_video_id is None
            and not chosen_by_person
            and previous is not None
            and previous.kind == "ai_pick"
            and bool(previous.video_id)
        )
        rejected = _read_rejected(research_dir)
        if redo and previous is not None and previous.video_id:
            rejected.add(previous.video_id)
            _check_cancel(ctx)
            _write_json(research_dir / REJECTED_FILE, {"video_ids": sorted(rejected)})
            notes.append(
                "Scanned again on request; the video picked before "
                f"('{previous.title}') was left out this time."
            )

        try:
            outcome = scan_competitors(
                channel,
                provider,
                cache,
                config=config,
                tabs=[tab_for_format(fmt)],
                force=redo,
                now=now,
                progress=progress,
                cancel=ctx.cancel,
            )
        except ResearchBlocked as exc:
            raise StageError(str(exc)) from exc
        _check_cancel(ctx)
        for status in outcome.statuses:
            if status.error:
                notes.append(f"{status.name}: {status.error}")

        history = history_for(cache, channel.slug, exclude_project_id=project.id)
        candidates = candidates_for(outcome, fmt, now=now, config=config, history=history)
        candidates_path = research_dir / CANDIDATES_FILE
        _check_cancel(ctx)
        _write_json(
            candidates_path,
            {
                "scanned_at": _iso(outcome.scanned_at),
                "format": fmt,
                "channels": [s.model_dump(mode="json") for s in outcome.statuses],
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
        )

        progress("Choosing the video", 91.0)
        reranked = False
        if manual_video_id is not None or chosen_by_person:
            pick = self._manual_candidate(
                manual_video_id or "", candidates, provider, cache, fmt, now, config
            )
            strategy = "person"
            reason = "Chosen by a person."
        else:
            rerank = self._rerank_for(ctx)
            eligible_history = history
            if rejected:
                eligible_history = UsedHistory(
                    video_ids=frozenset(history.video_ids | rejected), titles=history.titles
                )
            pick = pick_one(
                candidates,
                channel,
                eligible_history,
                strategy=config.picker.strategy,
                fresh_days=config.picker.fresh_days,
                rerank=rerank,
                rerank_top=config.picker.llm_rerank_top,
            )
            if pick is None:
                raise StageError(
                    "None of the competitor videos can be used right now: they are too new, "
                    "the wrong length, live streams, or already made by this channel. Add more "
                    "competitor channels, scan again later, or start from your own topic."
                )
            reranked = rerank is not None
            strategy = config.picker.strategy
            reason = explain_pick(pick, reranked=reranked)

        _check_cancel(ctx)
        progress("Reading the picked video", 93.0)
        details = self._details(pick, provider, cache, now, notes, config)
        if details is not None:
            pick = pick.model_copy(
                update={
                    "views": details.view_count if details.view_count is not None else pick.views,
                    "views_exact": details.view_count is not None,
                    "duration_s": details.duration_s or pick.duration_s,
                    "thumbnail_url": details.thumbnail_url or pick.thumbnail_url,
                }
            )

        _check_cancel(ctx)
        progress("Downloading the transcript", 96.0)
        transcript = self._transcript(pick, details, provider, channel, notes)
        _check_cancel(ctx)
        progress("Downloading the thumbnail", 98.0)
        thumbnail_path = self._thumbnail(pick, provider, research_dir / THUMBNAIL_FILE, notes)
        _check_cancel(ctx)

        record = ResearchPick(
            kind="manual_pick" if strategy == "person" else "ai_pick",
            video_id=pick.video_id,
            url=pick.url,
            title=pick.title,
            channel_name=pick.channel_name,
            channel_url=pick.channel_url,
            strategy=strategy,
            reason=reason,
            picked_at=now,
            candidate=pick,
        )
        pick_path = research_dir / PICK_FILE
        _write_json(pick_path, record)
        outputs = [candidates_path, pick_path]
        if details is not None:
            video_path = research_dir / VIDEO_FILE
            _write_json(video_path, details)
            outputs.append(video_path)
        transcript_path = research_dir / TRANSCRIPT_FILE
        _write_json(transcript_path, transcript)
        outputs.append(transcript_path)
        if thumbnail_path is not None:
            outputs.append(thumbnail_path)

        cache.replace_pick(channel.slug, pick.video_id, project.id, now)
        ctx.project.title = pick.title
        self._save_competitor_stats(ctx, outcome.statuses)

        payload = ResearchReviewPayload(
            source_kind=record.kind,
            format=fmt,
            scanned_at=outcome.scanned_at,
            channels=outcome.statuses,
            candidates=_highlight(candidates[:REVIEW_TOP_N], pick),
            pick=pick,
            pick_video_id=pick.video_id,
            transcript_available=transcript.available,
            thumbnail_file=thumbnail_path.name if thumbnail_path else None,
            notes=notes,
        )
        eligible = sum(1 for c in candidates if not c.excluded_reason)
        summary = (
            f"Picked '{pick.title}' from {pick.channel_name} "
            f"({pick.outlier_score:.1f}x, {pick.label}) out of {eligible} eligible videos "
            f"from {len(outcome.statuses)} competitor channel"
            f"{'s' if len(outcome.statuses) != 1 else ''}."
        )
        progress("Research done", 100.0)
        return StageResult(
            outputs=outputs, summary=summary, needs_review_payload=payload.as_payload()
        )

    # Helpers -------------------------------------------------------------------------------

    def _provider_for(self, ctx: StageContext, config: ResearchConfig) -> ResearchProvider:
        if self.provider is not None:
            return self.provider
        candidate = ctx.providers.get("research")
        if candidate is not None and hasattr(candidate, "list_tab"):
            return candidate
        return build_provider(ctx.settings, config=config)

    def _rerank_for(self, ctx: StageContext) -> RerankFn | None:
        if not rerank_enabled(ctx.settings):
            return None
        rerank = self.rerank or ctx.providers.get("research_rerank")
        return rerank if callable(rerank) else None

    def _manual_candidate(
        self,
        video_id: str,
        candidates: list[Candidate],
        provider: ResearchProvider,
        cache: ResearchCache,
        fmt: ResearchFormat,
        now: datetime,
        config: ResearchConfig,
    ) -> Candidate:
        video_id = video_id.strip()
        if not video_id:
            raise StageError("No video was chosen. Pick one from the list and try again.")
        for candidate in candidates:
            if candidate.video_id == video_id:
                return candidate
        # A video outside the competitor list: build a candidate from its details.
        try:
            details = cache.get_details(video_id, max_age_minutes=config.cache_minutes, now=now)
            if details is None:
                details = provider.video_details(video_id)
                cache.save_details(details)
        except ResearchError as exc:
            raise StageError(
                f"The chosen video ({video_id}) could not be read from YouTube: {exc}"
            ) from exc
        return candidate_from_details(details, fmt, now)

    def _details(
        self,
        pick: Candidate,
        provider: ResearchProvider,
        cache: ResearchCache,
        now: datetime,
        notes: list[str],
        config: ResearchConfig,
    ) -> VideoDetails | None:
        try:
            details = cache.get_details(
                pick.video_id, max_age_minutes=config.cache_minutes, now=now
            )
            if details is None:
                details = provider.video_details(pick.video_id)
                cache.save_details(details)
            return details
        except ResearchBlocked as exc:
            raise StageError(str(exc)) from exc
        except ResearchError as exc:
            notes.append(f"Exact numbers could not be read: {exc}")
            return None

    def _transcript(
        self,
        pick: Candidate,
        details: VideoDetails | None,
        provider: ResearchProvider,
        channel: Any,
        notes: list[str],
    ) -> TranscriptDoc:
        lang = _transcript_language(channel, pick, details)
        try:
            return provider.transcript(pick.video_id, lang)
        except ResearchBlocked as exc:
            raise StageError(str(exc)) from exc
        except ResearchError as exc:
            notes.append(f"No transcript: {exc}")
            return TranscriptDoc(
                video_id=pick.video_id, language=lang, source="none", error=str(exc)
            )

    def _thumbnail(
        self, pick: Candidate, provider: ResearchProvider, dest: Path, notes: list[str]
    ) -> Path | None:
        try:
            return provider.thumbnail(pick.video_id, dest)
        except ResearchBlocked as exc:
            raise StageError(str(exc)) from exc
        except ResearchError as exc:
            notes.append(f"No thumbnail: {exc}")
            return None

    def _save_competitor_stats(self, ctx: StageContext, statuses: list[Any]) -> None:
        shared_dir = getattr(ctx.settings, "resolved_shared_dir", None)
        if shared_dir is None or not statuses:
            return
        write_back_competitor_stats(ChannelStore(shared_dir), ctx.channel.slug, statuses)

    def _progress_fn(self, ctx: StageContext) -> Callable[[str, float | None], None]:
        """A sync callback usable from the worker thread; async callbacks are scheduled."""
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        def progress(message: str, pct: float | None = None) -> None:
            try:
                result = ctx.progress(message, pct)
            except Exception:
                log.debug("progress callback failed", exc_info=True)
                return
            if inspect.isawaitable(result):
                if loop is not None and not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(_await(result), loop)
                elif inspect.iscoroutine(result):
                    result.close()

        return progress


async def _await(awaitable: Any) -> None:
    await awaitable


def _check_cancel(ctx: StageContext) -> None:
    if ctx.cancel.is_set():
        raise ResearchCancelled(CANCELLED_MESSAGE)


def _with_progress(
    provider: ResearchProvider,
    progress: Callable[[str, float | None], None],
    *,
    wait_for_youtube: bool = True,
) -> ResearchProvider:
    """A copy of the provider that reports YouTube back-off waits as progress text.

    Inside a reviewer's request (``wait_for_youtube=False``) the copy never sleeps: a "slow
    down" answer fails at once with the plain-English message instead of hanging the request
    for ten minutes. Providers without back-off (the mock) are returned unchanged.
    """
    if not hasattr(provider, "on_wait") and not hasattr(provider, "backoff_seconds"):
        return provider
    try:
        clone = copy.copy(provider)
    except Exception:  # noqa: BLE001 - an exotic provider: use it as it is
        return provider
    if hasattr(clone, "on_wait"):

        def on_wait(seconds: float, reason: str) -> None:
            minutes = max(1, round(seconds / 60))
            progress(
                f"YouTube asked us to slow down ({reason}). Waiting {minutes} minute"
                f"{'s' if minutes != 1 else ''} before trying again.",
                None,
            )

        clone.on_wait = on_wait
    if not wait_for_youtube and hasattr(clone, "backoff_seconds"):
        clone.backoff_seconds = []
    return clone


def _read_pick(research_dir: Path) -> ResearchPick | None:
    path = research_dir / PICK_FILE
    if not path.is_file():
        return None
    try:
        return ResearchPick.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_rejected(research_dir: Path) -> set[str]:
    """Video ids the AI picked for this project before a redo (``rejected.json``)."""
    path = research_dir / REJECTED_FILE
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    ids = data.get("video_ids") if isinstance(data, dict) else None
    return {str(v) for v in ids if v} if isinstance(ids, list) else set()


def candidate_from_details(details: VideoDetails, fmt: ResearchFormat, now: datetime) -> Candidate:
    """A candidate for a video a person chose that is not on any competitor's tab."""
    views = details.view_count or 0
    age = age_days_of(details.published_at, now)
    return Candidate(
        video_id=details.video_id,
        url=details.url,
        title=details.title,
        channel_name=details.channel_name,
        channel_url=details.channel_url or "",
        channel_id=details.channel_id,
        format=fmt,
        views=views,
        views_exact=details.view_count is not None,
        published_at=details.published_at,
        age_days=int(age) if age is not None else 0,
        duration_s=details.duration_s,
        baseline_views=float(max(views, 1)),
        outlier_score=1.0,
        vpd=round(views_per_day(views, age), 2),
        vpd_ratio=1.0,
        sub_ratio=round(views / details.channel_follower_count, 6)
        if details.channel_follower_count
        else 0.0,
        label=label_for(1.0),
        thumbnail_url=details.thumbnail_url,
        used_before=False,
        excluded_reason=None,
        rank=0,
    )


def _transcript_language(channel: Any, pick: Candidate, details: VideoDetails | None) -> str:
    """The competitor's language if known, else what the video offers, else the channel's."""
    from .picker import competitor_language

    language = competitor_language(channel, pick) if channel is not None else None
    if language:
        return language_code(language)
    if details is not None:
        offered = details.caption_languages.manual + details.caption_languages.auto
        if details.language:
            return details.language
        if offered:
            return offered[0]
    return language_code(channel.channel.language if channel is not None else None)


def _highlight(candidates: list[Candidate], pick: Candidate) -> list[Candidate]:
    """Make sure the pick is in the top list the review panel shows."""
    if any(c.video_id == pick.video_id for c in candidates):
        return candidates
    return [*candidates, pick]


def _write_json(path: Path, data: Any) -> None:
    from ..storage.settings_store import atomic_write_text

    if hasattr(data, "model_dump_json"):
        text = data.model_dump_json(indent=2)
    else:
        import json

        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    atomic_write_text(path, text + "\n")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
