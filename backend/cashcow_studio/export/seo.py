"""The SEO pack: title, description, tags, chapters, pinned comment and hashtags in the
target language (docs/M3-M4-CONTRACT.md section 4).

Chapters come from the script's sections and the voice clock: the first sentence of each
section is looked up in ``05_voice/timing.json`` (``timing.json`` is the clock for everything
after the voice stage), formatted ``mm:ss``, the first always ``00:00``. YouTube needs at
least three chapters of ten seconds or more, so shorter lists are left empty. Claude (Sonnet,
prompt ``llm/prompts/seo.md``) writes the words; it also returns three thumbnail headlines
and whether the title's promise is made early in the script. Any field the model leaves blank
(the offline mock answers with an empty pack) is filled from the script, so the export is
always complete.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..llm.prompts import load_prompt
from ..llm.text import keywords_of, tokenize
from ..models.channel import Channel
from ..models.export import (
    MAX_DESCRIPTION_CHARS,
    MAX_HASHTAGS,
    MAX_TAGS_CHARS,
    MAX_TITLE_CHARS,
    MIN_CHAPTER_SECONDS,
    MIN_CHAPTERS,
    Chapter,
    SeoLLMOutput,
    SeoPack,
)
from ..models.script import ScriptDoc

log = logging.getLogger(__name__)

TASK = "seo"
SEO_MODEL = "claude-sonnet-5-5"
SEO_EFFORT = "medium"
MAX_SCRIPT_CHARS = 60_000
MAX_TAGS = 25
TITLE_SHARE = 0.2


# Time and chapters -----------------------------------------------------------------------------


def format_time(seconds: float) -> str:
    """``mm:ss`` (``h:mm:ss`` from one hour), rounded down to whole seconds."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_time(text: str) -> float | None:
    parts = (text or "").strip().split(":")
    if not parts or not all(p.strip().isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    seconds = 0
    for value in numbers:
        seconds = seconds * 60 + value
    return float(seconds)


def sentence_starts(timing: dict[str, Any] | None) -> dict[str, float]:
    """``sentence id -> start_s`` from ``timing.json`` (loosely read)."""
    starts: dict[str, float] = {}
    for row in (timing or {}).get("sentences") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            starts[str(row["id"])] = float(row.get("start_s") or 0.0)
        except (TypeError, ValueError):
            continue
    return starts


def timing_duration(timing: dict[str, Any] | None) -> float | None:
    value = (timing or {}).get("duration_s")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def chapters_from(
    script: ScriptDoc,
    timing: dict[str, Any] | None,
    fmt: str,
    duration_s: float | None = None,
) -> list[Chapter]:
    """One chapter per script section at the start of its first sentence.

    Shorts get no chapters. With no timing file the starts are estimated from the word
    position; chapters closer than ten seconds to the previous one are dropped, as YouTube
    ignores them, and fewer than three chapters means none at all.
    """
    if fmt == "shorts" or not script.sections:
        return []
    starts = sentence_starts(timing)
    total_words = sum(len(p.text.split()) for p in script.paragraphs()) or 1
    duration = duration_s or timing_duration(timing) or (
        total_words * 60.0 / max(60, script.speaking_rate_wpm or 150)
    )
    rows: list[tuple[float, str]] = []
    words_before = 0
    for section in script.sections:
        first_id = next((s.id for p in section.paragraphs for s in p.sentences), None)
        if first_id is not None and first_id in starts:
            start = starts[first_id]
        else:
            start = duration * words_before / total_words
        words_before += sum(len(p.text.split()) for p in section.paragraphs)
        name = " ".join((section.name or "").split()) or f"Part {len(rows) + 1}"
        rows.append((max(0.0, start), name))
    rows.sort(key=lambda r: r[0])
    kept: list[Chapter] = []
    last = -1e9
    for index, (start, name) in enumerate(rows):
        if index == 0:
            start = 0.0
        if start - last < MIN_CHAPTER_SECONDS - 1e-9:
            continue
        if duration and index > 0 and duration - start < MIN_CHAPTER_SECONDS:
            continue
        kept.append(Chapter(time=format_time(start), title=name))
        last = start
    if len(kept) < MIN_CHAPTERS:
        return []
    return kept


def merge_chapter_titles(computed: list[Chapter], suggested: list[Chapter]) -> list[Chapter]:
    """Keep the computed times; take the model's titles when it kept the same count."""
    if not computed or len(suggested) != len(computed):
        return computed
    merged: list[Chapter] = []
    for base, other in zip(computed, suggested, strict=True):
        title = " ".join((other.title or "").split()) or base.title
        merged.append(Chapter(time=base.time, title=title[:100]))
    return merged


# Text helpers ----------------------------------------------------------------------------------


def script_text(script: ScriptDoc) -> str:
    return script.text()


def title_promise_heuristic(title: str, text: str, share: float = TITLE_SHARE) -> bool:
    """True when a keyword of the title appears in the first ``share`` of the script."""
    tokens = tokenize(text)
    if not tokens:
        return False
    head = set(tokens[: max(1, int(len(tokens) * share))])
    keys = [k.lower() for k in keywords_of(title)]
    if not keys:
        return True
    return any(k in head or any(t.startswith(k[:5]) for t in head if len(k) >= 5) for k in keys)


def trim_tags(tags: list[str], budget: int = MAX_TAGS_CHARS, limit: int = MAX_TAGS) -> list[str]:
    """Unique, clean tags whose total length (with separators) stays within the budget."""
    out: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw in tags:
        tag = " ".join(str(raw).replace("#", "").split()).strip(" ,")[:60]
        key = tag.casefold()
        if not tag or key in seen:
            continue
        extra = len(tag) + (1 if out else 0)
        if used + extra > budget or len(out) >= limit:
            break
        seen.add(key)
        out.append(tag)
        used += extra
    return out


def clean_hashtags(items: list[str], limit: int = MAX_HASHTAGS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        word = re.sub(r"[^\w]+", "", str(raw).replace("#", "").replace(" ", ""))
        if not word or word.casefold() in seen:
            continue
        seen.add(word.casefold())
        out.append("#" + word)
        if len(out) >= limit:
            break
    return out


def enforce_limits(pack: SeoPack, notes: list[str] | None = None) -> SeoPack:
    """YouTube's limits: title 100, description 5000, tags 500 characters, 15 hashtags."""
    notes = notes if notes is not None else []
    title = " ".join(pack.title.split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
        notes.append(f"The title was cut to {MAX_TITLE_CHARS} characters.")
    description = pack.description.strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"
        notes.append(f"The description was cut to {MAX_DESCRIPTION_CHARS} characters.")
    tags = trim_tags(pack.tags)
    if len(tags) < len({t.casefold() for t in pack.tags if t.strip()}):
        notes.append(f"Tags were trimmed to stay within {MAX_TAGS_CHARS} characters.")
    hashtags = clean_hashtags(pack.hashtags)
    chapters = [
        Chapter(time=c.time, title=" ".join(c.title.split())[:100])
        for c in pack.chapters
        if parse_time(c.time) is not None and c.title.strip()
    ]
    if chapters and parse_time(chapters[0].time) != 0.0:
        chapters[0] = Chapter(time="00:00", title=chapters[0].title)
    if 0 < len(chapters) < MIN_CHAPTERS:
        notes.append("Fewer than three chapters; YouTube shows none, so the list was left out.")
        chapters = []
    return SeoPack(
        title=title or pack.title,
        description=description,
        tags=tags,
        chapters=chapters,
        pinned_comment=" ".join(pack.pinned_comment.split()),
        hashtags=hashtags,
    )


# Fallbacks (the offline mock, or a partial answer) ---------------------------------------------


def fallback_description(
    title: str, script: ScriptDoc, chapters: list[Chapter], channel_name: str
) -> str:
    paragraphs = script.paragraphs()
    opening = " ".join(paragraphs[0].text.split()) if paragraphs else title
    sentences = re.split(r"(?<=[.!?])\s+", opening)
    hook = " ".join(sentences[:2]).strip()
    parts = [hook or title]
    if len(script.sections) > 1:
        names = ", ".join(s.name for s in script.sections if s.name)
        parts.append(f"In this video: {names}.")
    if chapters:
        parts.append("\n".join(f"{c.time} {c.title}" for c in chapters))
    parts.append(f"Thank you for watching {channel_name}.".strip())
    return "\n\n".join(p for p in parts if p)


def fallback_tags(title: str, script: ScriptDoc, niche: str) -> list[str]:
    tags = list(keywords_of(title, limit=8))
    tags.append(title)
    tags.extend(keywords_of(niche, limit=4))
    counts: dict[str, int] = {}
    for token in tokenize(script.text()):
        if len(token) >= 5:
            counts[token] = counts.get(token, 0) + 1
    tags.extend(sorted(counts, key=lambda t: (-counts[t], t))[:10])
    return trim_tags(tags)


def fallback_pinned_comment(title: str, language: str) -> str:
    del language  # the fixed line is English; the model writes it in the target language
    return f"What would you have done? Tell us in the comments. ({title})"


def fallback_pack(
    title: str, script: ScriptDoc, chapters: list[Chapter], channel: Channel
) -> SeoPack:
    return SeoPack(
        title=title,
        description=fallback_description(title, script, chapters, channel.channel.name),
        tags=fallback_tags(title, script, channel.channel.niche),
        chapters=chapters,
        pinned_comment=fallback_pinned_comment(title, channel.channel.language),
        hashtags=clean_hashtags(keywords_of(title, limit=5) + keywords_of(channel.channel.niche,
                                                                            limit=3)),
    )


# Building the pack ------------------------------------------------------------------------------


@dataclass
class SeoResult:
    pack: SeoPack
    headlines: list[str] = field(default_factory=list)
    title_promise_early: bool | None = None
    title_promise_note: str = ""
    model: str = ""
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)
    from_model: bool = False


def _model_choice(llm: Any) -> str | None:
    models = getattr(getattr(llm, "config", None), "models", None)
    if isinstance(models, dict) and models.get(TASK):
        return None  # config/llm.yaml names the model for this task
    return SEO_MODEL


async def build_seo_pack(
    llm: Any,
    *,
    title: str,
    script: ScriptDoc,
    timing: dict[str, Any] | None,
    channel: Channel,
    language: str,
    fmt: str,
    max_headline_words: int,
    project_id: str,
    notes: list[str] | None = None,
    duration_s: float | None = None,
) -> SeoResult:
    """The SEO pack from Claude, completed from the script wherever the answer is blank."""
    computed = chapters_from(script, timing, fmt, duration_s)
    text = script_text(script)
    if len(text) > MAX_SCRIPT_CHARS:
        text = text[:MAX_SCRIPT_CHARS] + "\n[... script shortened for this request ...]"
    reviewer_notes = [n for n in (notes or []) if n.strip()]
    variables: dict[str, Any] = {
        "title": title,
        "language": language,
        "format": "Shorts (vertical)" if fmt == "shorts" else "long-form",
        "channel_name": channel.channel.name,
        "niche": channel.channel.niche or "(not set)",
        "audience": channel.channel.audience or "(not set)",
        "brand_notes": channel.thumbnail.must_include or "(none)",
        "max_headline_words": max_headline_words,
        "chapters": [f"{c.time} {c.title}" for c in computed] or ["(no chapters: too short)"],
        "notes": reviewer_notes or ["(none)"],
        "script_text": text,
    }
    prompt = load_prompt(TASK)
    result_notes: list[str] = []
    output = SeoLLMOutput()
    model_name = ""
    cost = 0.0
    try:
        output, usage = await llm.complete(
            TASK,
            prompt.render("system", variables),
            prompt.render("user", variables),
            SeoLLMOutput,
            model=_model_choice(llm),
            effort=SEO_EFFORT,
            variables={**variables, "script_text": text[:2000]},
            project_id=project_id,
            stage="export",
        )
        model_name = str(getattr(usage, "model", "") or "")
        cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001 - the pack is still built from the script
        log.warning("The SEO request failed; building the pack from the script: %s", exc)
        result_notes.append(
            f"The writing model could not write the metadata ({str(exc)[:160]}); it was "
            "built from the script instead. Redo the export step to try again."
        )
    fallback = fallback_pack(title, script, computed, channel)
    from_model = bool(output.description.strip() or output.tags)
    pack = SeoPack(
        title=" ".join(output.title.split()) or title,
        description=output.description.strip() or fallback.description,
        tags=output.tags or fallback.tags,
        chapters=merge_chapter_titles(computed, output.chapters),
        pinned_comment=output.pinned_comment.strip() or fallback.pinned_comment,
        hashtags=output.hashtags or fallback.hashtags,
    )
    if not from_model:
        result_notes.append(
            "The metadata was built from the script (the writing model returned nothing "
            "to use)."
        )
    pack = enforce_limits(pack, result_notes)
    promise = output.title_promise_early
    note = " ".join(output.title_promise_note.split())
    if promise is None:
        promise = title_promise_heuristic(title, script.text())
        note = note or (
            "Checked by keyword: a word from the title "
            + ("appears" if promise else "does not appear")
            + " in the first fifth of the script."
        )
    return SeoResult(
        pack=pack,
        headlines=[h for h in output.headlines if str(h).strip()],
        title_promise_early=promise,
        title_promise_note=note,
        model=model_name,
        cost_usd=cost,
        notes=result_notes,
        from_model=from_model,
    )
