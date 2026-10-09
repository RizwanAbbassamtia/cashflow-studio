"""Research endpoints: start a competitor scan and read the ranked candidates.

See docs/M1-M2-CONTRACT.md section 1 (table) and section 2. The scan runs as a background
job (``research/jobs.py``) that ``GET /api/jobs/{job_id}`` reports on; the candidates come
from the SQLite cache, so reading them never touches YouTube.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..models.channel import Channel
from ..models.research import CandidatesResponse, ResearchFormat, ScanRequest, tab_for_format
from ..research.cache import ResearchCache
from ..research.config import cached_research_config
from ..research.jobs import start_scan_job
from ..research.picker import pick_one
from ..research.scanner import cached_outcome, candidates_for, default_format, history_for
from ..storage.channel_store import (
    ChannelNotFound,
    ChannelStore,
    ChannelStoreError,
    InvalidSlug,
)
from .deps import ChannelStoreDep, SettingsDep

router = APIRouter(prefix="/api", tags=["research"])

MAX_LIMIT = 500


def _load_channel(store: ChannelStore, slug: str) -> Channel:
    try:
        return store.get(slug)
    except (ChannelNotFound, InvalidSlug) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The channel file could not be read: {exc}"
        ) from exc


@router.post("/channels/{slug}/research/scan", status_code=202)
def start_scan(
    slug: str,
    store: ChannelStoreDep,
    settings: SettingsDep,
    body: ScanRequest | None = None,
) -> dict[str, str]:
    channel = _load_channel(store, slug)
    if not channel.competitors:
        raise HTTPException(
            status_code=422,
            detail="Add at least one competitor channel in Channel Setup before scanning.",
        )
    job = start_scan_job(
        settings=settings, channel=channel, request=body or ScanRequest(), store=store
    )
    return {"job_id": job.id}


@router.get("/channels/{slug}/research/candidates", response_model=CandidatesResponse)
def get_candidates(
    slug: str,
    store: ChannelStoreDep,
    settings: SettingsDep,
    fmt: Annotated[ResearchFormat | None, Query(alias="format")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> CandidatesResponse:
    channel = _load_channel(store, slug)
    chosen_format: ResearchFormat = fmt or default_format(channel)
    cache = ResearchCache(settings.app_data_dir)
    config = cached_research_config()
    outcome = cached_outcome(channel, cache, tabs=[tab_for_format(chosen_format)])
    history = history_for(cache, slug)
    candidates = candidates_for(
        outcome, chosen_format, now=datetime.now(UTC), config=config, history=history
    )
    # The same pick the research stage makes, so the page highlights the video production
    # really starts from (the picker prefers the channel's language and fresh videos, which
    # is not always rank 1).
    pick = pick_one(
        candidates,
        channel,
        history,
        strategy=config.picker.strategy,
        fresh_days=config.picker.fresh_days,
    )
    return CandidatesResponse(
        scanned_at=outcome.scanned_at,
        format=chosen_format,
        channels=outcome.statuses,
        candidates=candidates[:limit],
        pick=pick,
        pick_video_id=pick.video_id if pick is not None else None,
    )
