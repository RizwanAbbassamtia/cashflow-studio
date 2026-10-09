"""Title stage: seven title options from the picked video (or the typed topic), gated.

Reads ``01_research/pick.json`` / ``video.json`` (or the project's own topic), the channel's
title framework (or the built-in default in ``llm/prompts/title.md``) and the channel's recent
titles (``title_history``), asks Claude for seven options, measures each option (length,
similarity to the source and to recent titles, kept keywords), flags the ones that fail a
gate and writes ``02_title/title.json``. The recommended option becomes the project title
until a reviewer picks another one (``apply_edits``), and goes into ``title_history``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...llm.frameworks import framework_cache_dir, framework_text
from ...llm.log import add_missing_columns, utc_now_iso
from ...llm.prompts import load_prompt
from ...llm.text import contains_keyword, keywords_of, similarity
from ...models.project import Project, StageName
from ...models.title import (
    TitleApproveEdits,
    TitleDoc,
    TitleLLMOutput,
    TitleReviewPayload,
    TitleSource,
    TitleVariant,
)
from ...policy import gates
from ...storage import db
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult

log = logging.getLogger(__name__)

TITLE_FILE = "title.json"
TASK = "title"
HISTORY_LIMIT = 30
MAX_TITLE_LENGTH = 200

CREATE_TITLE_HISTORY = """
CREATE TABLE IF NOT EXISTS title_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    project_id TEXT,
    created_at TEXT NOT NULL DEFAULT ''
)
"""
TITLE_HISTORY_COLUMNS = {
    "channel_slug": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}


# title_history ------------------------------------------------------------------------------


def ensure_title_history(app_data_dir: Path) -> None:
    db.ensure_table(app_data_dir, CREATE_TITLE_HISTORY)
    with db.connect(app_data_dir) as conn:
        add_missing_columns(conn, "title_history", TITLE_HISTORY_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS title_history_channel ON title_history (channel_slug, id)"
        )


def recent_titles(
    app_data_dir: Path,
    channel_slug: str,
    limit: int = HISTORY_LIMIT,
    exclude_project_id: str | None = None,
) -> list[str]:
    """The channel's most recent titles, newest first, without this project's own row."""
    ensure_title_history(app_data_dir)
    with db.connect(app_data_dir) as conn:
        rows = conn.execute(
            "SELECT title, project_id FROM title_history WHERE channel_slug = ? "
            "ORDER BY id DESC LIMIT ?",
            (channel_slug, limit + 5),
        ).fetchall()
    titles: list[str] = []
    for row in rows:
        if exclude_project_id and row["project_id"] == exclude_project_id:
            continue
        title = (row["title"] or "").strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def upsert_title_history(
    app_data_dir: Path, channel_slug: str, title: str, project_id: str
) -> None:
    """One row per project: the current title of this project on this channel."""
    ensure_title_history(app_data_dir)
    with db.connect(app_data_dir) as conn:
        conn.execute(
            "DELETE FROM title_history WHERE project_id = ? AND channel_slug = ?",
            (project_id, channel_slug),
        )
        conn.execute(
            "INSERT INTO title_history (channel_slug, title, project_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (channel_slug, title.strip(), project_id, utc_now_iso()),
        )


# Inputs --------------------------------------------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_source(folder: Path, project: Project) -> TitleSource:
    """What the titles are written from: the picked video, or the typed topic."""
    research = folder / "01_research"
    pick = read_json(research / "pick.json") or {}
    video = read_json(research / "video.json") or {}
    own_topic = project.source.kind == "own_topic" or pick.get("kind") == "own_topic"
    if not own_topic and not pick and not video:
        # An AI or hand pick without research files: the project title is only the
        # placeholder "AI pick (research pending)", never something to write titles for.
        raise StageError(
            "Research produced no picked video for this project. Redo the research step, "
            "choose a video, or start again from your own topic."
        )
    if own_topic:
        topic = (
            (project.source.topic_text or "").strip()
            or (pick.get("topic_text") or "").strip()
            or project.title.strip()
        )
        if not topic:
            raise StageError(
                "There is no topic to write titles for. Run the research step or type a topic."
            )
        return TitleSource(kind="own_topic", title=topic)
    candidate = pick.get("candidate") if isinstance(pick.get("candidate"), dict) else {}
    title = (pick.get("title") or video.get("title") or candidate.get("title") or "").strip()
    if not title:
        raise StageError(
            "The research step did not record the picked video's title. Run research again."
        )
    views = candidate.get("views") or video.get("view_count")
    score = candidate.get("outlier_score")
    return TitleSource(
        kind="manual_pick" if project.source.kind == "manual_pick" else "ai_pick",
        title=title,
        video_id=pick.get("video_id") or video.get("video_id"),
        url=pick.get("url") or video.get("url"),
        channel_name=pick.get("channel_name") or video.get("channel_name"),
        views=int(views) if isinstance(views, (int, float)) else None,
        outlier_score=round(float(score), 2) if isinstance(score, (int, float)) else None,
    )


def source_stats(source: TitleSource) -> str:
    parts = []
    if source.channel_name:
        parts.append(f"from {source.channel_name}")
    if source.views is not None:
        parts.append(f"{source.views:,} views")
    if source.outlier_score is not None:
        parts.append(f"{source.outlier_score:g}x the channel's usual views")
    return "Performance: " + ", ".join(parts) if parts else ""


# Measuring and flagging ----------------------------------------------------------------------


def measure_variants(
    output: TitleLLMOutput,
    source: TitleSource,
    history: list[str],
    keywords: list[str],
) -> list[TitleVariant]:
    """Attach index, similarity scores, length and plain-English flags to every option."""
    max_chars = int(gates.params("title.length").get("max_chars", 70))
    max_source = float(gates.params("title.similarity_source").get("max_ratio", 0.8))
    max_history = float(gates.params("title.similarity_history").get("max_ratio", 0.8))
    min_keywords = int(gates.params("title.keywords").get("min_keywords", 1))
    wanted = int(gates.params("title.count").get("count", 7))
    variants: list[TitleVariant] = []
    for index, raw in enumerate(output.variants[:wanted]):
        title = " ".join(raw.title.split())
        to_source = similarity(title, source.title)
        closest, to_history = "", 0.0
        for previous in history:
            ratio = similarity(title, previous)
            if ratio > to_history:
                closest, to_history = previous, ratio
        kept = [k for k in keywords if contains_keyword(title, k)]
        flags: list[str] = []
        if len(title) >= max_chars:
            flags.append(f"Longer than {max_chars - 1} characters ({len(title)}).")
        if source.kind != "own_topic" and to_source >= max_source:
            flags.append(f"Too close to the competitor's title ({to_source * 100:.0f}% similar).")
        if history and to_history >= max_history:
            flags.append(
                f"Too close to a recent title of this channel: '{closest}' "
                f"({to_history * 100:.0f}% similar)."
            )
        if keywords and len(kept) < min_keywords:
            flags.append("Keeps no keyword from the source title.")
        if not title:
            flags.append("Empty title.")
        variants.append(
            TitleVariant(
                index=index,
                title=title,
                formula=raw.formula,
                emotional_trigger=raw.emotional_trigger,
                curiosity_trigger=raw.curiosity_trigger,
                hidden_gap=raw.hidden_gap,
                viral_score=max(1, min(10, int(raw.viral_score))),
                why_it_outperforms=raw.why_it_outperforms,
                keywords_kept=kept or [k for k in raw.keywords_kept if contains_keyword(title, k)],
                similarity_to_source=to_source,
                similarity_to_history=to_history,
                length=len(title),
                flags=flags,
            )
        )
    return variants


def choose_recommended(variants: list[TitleVariant], suggested: int | None) -> int | None:
    """The model's pick when it is clean, else the clean option with the best score."""
    clean = [v for v in variants if v.clean]
    if not clean:
        return None
    if suggested is not None:
        for variant in clean:
            if variant.index == suggested:
                return variant.index
    best = max(clean, key=lambda v: (v.viral_score, -v.similarity_to_source, -v.index))
    return best.index


def gate_context(doc: TitleDoc) -> dict[str, Any]:
    return {
        "variants": [
            {
                "title": v.title,
                "length": v.length,
                "similarity_to_source": v.similarity_to_source,
                "similarity_to_history": v.similarity_to_history,
                "keywords_kept": bool(v.keywords_kept),
                "flags": list(v.flags),
            }
            for v in doc.variants
        ],
        "source_is_competitor": doc.source.kind != "own_topic",
        "history_count": doc.history_compared,
        "recommended_index": doc.recommended_index,
    }


# Files -----------------------------------------------------------------------------------------


def read_title_doc(stage_dir: Path) -> TitleDoc | None:
    path = stage_dir / TITLE_FILE
    if not path.is_file():
        return None
    try:
        return TitleDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise StageError(f"The file {path} could not be read: {exc}") from exc


def write_title_doc(stage_dir: Path, doc: TitleDoc) -> Path:
    path = stage_dir / TITLE_FILE
    atomic_write_text(path, doc.model_dump_json(indent=2) + "\n")
    return path


def review_payload(doc: TitleDoc, history: list[str]) -> dict[str, Any]:
    rules = {r.id: {"title": r.title, "severity": r.severity, "parameters": r.parameters}
             for r in gates.rules_for("title")}
    return TitleReviewPayload(title=doc, recent_titles=history, rules=rules).model_dump(mode="json")


def _llm(ctx: StageContext) -> Any:
    client = ctx.providers.get("llm")
    if client is None:
        raise StageError("No writing model is set up. Check Settings > Models and providers.")
    return client


# The stage -------------------------------------------------------------------------------------


class TitleStage:
    name = StageName.title

    async def run(self, ctx: StageContext) -> StageResult:
        llm = _llm(ctx)
        project, channel = ctx.project, ctx.channel
        stage_dir = ctx.stage_dir(StageName.title)
        await ctx.report("Reading the picked video", 5)
        source = load_source(ctx.folder, project)
        keywords = keywords_of(source.title)
        prompt = load_prompt(TASK)
        framework = framework_text(
            channel, ("title",), project.format, ctx.settings.resolved_shared_dir,
            prompt.default_framework,
            cache_dir=framework_cache_dir(ctx.settings.app_data_dir),
        )
        history = recent_titles(
            ctx.settings.app_data_dir, project.channel_slug, exclude_project_id=project.id
        )
        notes = [n for n in ctx.notes if n.strip()]
        if framework.note:
            notes.append(framework.note)

        variables: dict[str, Any] = {
            "source_kind_note": (
                "a topic typed by the team" if source.kind == "own_topic"
                else "a competitor video that outperformed its channel"
            ),
            "source_title": source.title,
            "source_stats": source_stats(source),
            "channel_name": channel.channel.name,
            "niche": channel.channel.niche or "(not set)",
            "audience": channel.channel.audience or "(not set)",
            "language": project.language,
            "format": "Shorts (vertical, under 3 minutes)" if project.format == "shorts"
            else "long-form video",
            "keywords": ", ".join(keywords) if keywords else "(none found; keep the subject)",
            "keywords_list": keywords,
            "recent_titles": history or ["(none yet)"],
            "notes": [n for n in ctx.notes if n.strip()] or ["(none)"],
            "topic": source.title,
        }
        system = [framework.text, prompt.render("system", variables)]
        cost = 0.0
        model = ""
        doc: TitleDoc | None = None
        for attempt in (1, 2):
            await ctx.report(
                "Writing 7 title options" if attempt == 1 else "Writing 7 new options", 30 * attempt
            )
            user = prompt.render("user", variables)
            output, usage = await llm.complete(
                TASK, system, user, TitleLLMOutput, variables=variables,
                project_id=project.id, stage=StageName.title.value,
            )
            cost += usage.cost_usd
            model = usage.model
            variants = measure_variants(output, source, history, keywords)
            doc = TitleDoc(
                variants=variants,
                recommended_index=choose_recommended(variants, output.recommended_index),
                source_title=source.title,
                source=source,
                generated_at=datetime.now(UTC),
                model=model,
                language=project.language,
                format=project.format,
                framework_source=framework.source,
                framework_name=framework.framework.name if framework.framework else None,
                history_compared=len(history),
                notes=list(notes),
            )
            if doc.recommended_index is not None:
                break
            failures = sorted({flag for v in variants for flag in v.flags})
            variables["notes"] = list(variables["notes"]) + [
                "Every option of the previous attempt failed a check: " + " ".join(failures)
                + " Write seven different options that avoid these problems."
            ]
        assert doc is not None
        results = gates.evaluate("title", gate_context(doc))
        doc.gate_results = gates.to_dicts(results)
        path = write_title_doc(stage_dir, doc)
        blocking = gates.blocking_reasons(results)
        if blocking or doc.recommended_index is None:
            raise GateBlocked(
                blocking or ["Every title option failed a check."], cost_usd=round(cost, 6)
            )
        recommended = doc.variant(doc.recommended_index)
        assert recommended is not None
        project.title = recommended.title
        upsert_title_history(
            ctx.settings.app_data_dir, project.channel_slug, recommended.title, project.id
        )
        await ctx.report("Title options ready for review", 100)
        clean = sum(1 for v in doc.variants if v.clean)
        return StageResult(
            outputs=[path],
            summary=f"{len(doc.variants)} title options ({clean} pass every check). "
            f"Recommended: {recommended.title}",
            cost_usd=round(cost, 6),
            needs_review_payload=review_payload(doc, history),
            gate_results=doc.gate_results,
        )

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits ``{chosen_index | title_text}``: set the project title."""
        try:
            edits = TitleApproveEdits.model_validate(ctx.edits or {})
        except ValidationError as exc:
            raise StageError(f"The title edits are not valid: {exc.errors()[0]['msg']}") from exc
        stage_dir = ctx.stage_dir(StageName.title)
        doc = read_title_doc(stage_dir)
        if doc is None:
            if not (edits.title_text and edits.title_text.strip()):
                raise StageError(
                    "The title step has not produced its options yet. Type a title to use "
                    "instead, or redo the step."
                )
            # The model never answered (for example the connection failed) but the reviewer
            # typed a title: record it in a title.json of its own.
            doc = _typed_only_doc(ctx)
        chosen_index: int | None
        if edits.title_text and edits.title_text.strip():
            chosen_title = " ".join(edits.title_text.split())
            if len(chosen_title) > MAX_TITLE_LENGTH:
                raise StageError(f"The title is longer than {MAX_TITLE_LENGTH} characters.")
            match = next((v for v in doc.variants if v.title == chosen_title), None)
            chosen_index = match.index if match else None
        elif edits.chosen_index is not None:
            variant = doc.variant(edits.chosen_index)
            if variant is None:
                raise StageError(f"There is no title option number {edits.chosen_index + 1}.")
            chosen_title, chosen_index = variant.title, variant.index
        else:
            return None
        doc.chosen_index = chosen_index
        doc.chosen_title = chosen_title
        path = write_title_doc(stage_dir, doc)
        ctx.project.title = chosen_title
        upsert_title_history(
            ctx.settings.app_data_dir, ctx.project.channel_slug, chosen_title, ctx.project.id
        )
        history = recent_titles(
            ctx.settings.app_data_dir, ctx.project.channel_slug, exclude_project_id=ctx.project.id
        )
        return StageResult(
            outputs=[path],
            summary=f"Title chosen: {chosen_title}",
            needs_review_payload=review_payload(doc, history),
            gate_results=doc.gate_results,
        )


def _typed_only_doc(ctx: StageContext) -> TitleDoc:
    """A ``title.json`` for a title typed by hand when the model produced no options."""
    project = ctx.project
    try:
        source = load_source(ctx.folder, project)
    except StageError:
        kind = "own_topic" if project.source.kind == "own_topic" else project.source.kind
        source = TitleSource(
            kind=kind,  # type: ignore[arg-type]
            title=(project.source.topic_text or project.title or "").strip() or "(unknown)",
            video_id=project.source.video_id,
            url=project.source.video_url,
        )
    return TitleDoc(
        variants=[],
        recommended_index=None,
        source_title=source.title,
        source=source,
        generated_at=datetime.now(UTC),
        model="person",
        language=project.language,
        format=project.format,
        notes=["No title options were generated; the title was typed by the reviewer."],
    )


def load_review_payload(
    folder: Path, app_data_dir: Path, channel_slug: str, project_id: str
) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    doc = read_title_doc(folder / "02_title")
    if doc is None:
        return {}
    history = recent_titles(app_data_dir, channel_slug, exclude_project_id=project_id)
    return review_payload(doc, history)
