"""The AI pick: the one competitor video production starts from.

``pick_one`` is pure. The default strategy ``top_outlier_fresh`` takes the highest-scoring
candidate that is eligible and not used before, preferring videos from a competitor in the
channel's language and videos from the last ``fresh_days`` days (each preference is dropped
when it would leave nothing). The optional LLM rerank is a plain callable the caller passes
in, so this module never depends on the LLM package; it is only used when
``CCS_RESEARCH_LLM_RERANK=1`` (see :func:`rerank_enabled`).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from ..models.channel import Channel
from ..models.research import Candidate
from .outliers import UsedHistory, rank_key

DEFAULT_STRATEGY = "top_outlier_fresh"
STRATEGIES: tuple[str, ...] = (DEFAULT_STRATEGY,)

RerankFn = Callable[[list[Candidate], Channel], Sequence[str] | None]
"""``rerank(top_candidates, channel)`` returns video ids best-first, or ``None`` to keep the
order. Exceptions are swallowed: a failed rerank never blocks the pick."""


def rerank_enabled(settings: Any = None) -> bool:
    """``CCS_RESEARCH_LLM_RERANK=1`` (or ``0``) wins; else ``settings.research.llm_rerank``."""
    flag = os.environ.get("CCS_RESEARCH_LLM_RERANK", "").strip()
    if flag:
        return flag == "1"
    research = getattr(settings, "research", None)
    return bool(getattr(research, "llm_rerank", False))


def eligible_candidates(
    candidates: Iterable[Candidate], history: UsedHistory | None = None
) -> list[Candidate]:
    history = history or UsedHistory()
    return [
        c
        for c in candidates
        if not c.excluded_reason and not c.used_before and not history.contains(c.video_id, c.title)
    ]


def prefer(pool: list[Candidate], keep: Callable[[Candidate], bool]) -> list[Candidate]:
    """The candidates matching ``keep``, or the whole pool when none does."""
    subset = [c for c in pool if keep(c)]
    return subset or pool


def normalise_channel_url(url: str | None) -> str:
    text = str(url or "").strip().rstrip("/").casefold()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith("www."):
        text = text[len("www.") :]
    return text


def competitor_language(channel: Channel, candidate: Candidate) -> str | None:
    """The language of the competitor the candidate came from, if it is on the channel."""
    wanted = normalise_channel_url(candidate.channel_url)
    for competitor in channel.competitors:
        if normalise_channel_url(str(competitor.url)) == wanted:
            return competitor.language
        if competitor.id and candidate.channel_id and competitor.id == candidate.channel_id:
            return competitor.language
    return None


def pick_one(
    candidates: Iterable[Candidate],
    channel: Channel,
    history: UsedHistory | None = None,
    *,
    strategy: str = DEFAULT_STRATEGY,
    fresh_days: int = 90,
    rerank: RerankFn | None = None,
    rerank_top: int = 10,
) -> Candidate | None:
    """The AI pick, or ``None`` when nothing is eligible."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown pick strategy '{strategy}'. Known: {', '.join(STRATEGIES)}.")
    pool = eligible_candidates(candidates, history)
    if not pool:
        return None
    language = channel.channel.language
    pool = prefer(pool, lambda c: competitor_language(channel, c) == language)
    pool = prefer(pool, lambda c: c.age_days <= fresh_days)
    pool.sort(key=rank_key)
    if rerank is not None:
        top = pool[:rerank_top]
        try:
            order = rerank(top, channel) or []
        except Exception:
            order = []
        by_id = {c.video_id: c for c in top}
        for video_id in order:
            if video_id in by_id:
                return by_id[video_id]
    return pool[0]


def explain_pick(candidate: Candidate, *, reranked: bool = False) -> str:
    """One plain-English sentence for ``pick.json`` and the review panel."""
    how = "Chosen by the AI rerank" if reranked else "Highest outlier score"
    score = f"{candidate.outlier_score:.1f}x its channel's usual views"
    when = (
        f"published {candidate.age_days} day{'s' if candidate.age_days != 1 else ''} ago"
        if candidate.published_at
        else "publish date unknown"
    )
    return f"{how}: {score} ({candidate.label}), {when} on {candidate.channel_name}."
