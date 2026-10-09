"""Script stage: the narration, its spoken form and the originality gates.

Flow: title and target length -> structure-only summary of the competitor transcript (Sonnet)
-> full script (Opus, streamed) with locked paragraphs kept verbatim -> sentence ids ->
``script.json`` + ``script.md`` -> spoken form (``speech.json``) -> policy reading (Sonnet)
-> 8-gram overlap against the transcript and the channel's last 30 scripts ->
``originality.json`` -> gates. A failing ``block`` gate raises ``GateBlocked`` after the files
are written, so the reviewer sees everything and can redo with notes or override.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...llm.config import speaking_rate_wpm
from ...llm.frameworks import framework_cache_dir, framework_text
from ...llm.log import add_missing_columns, utc_now_iso
from ...llm.prompts import load_prompt
from ...llm.text import (
    count_words,
    fingerprint,
    ngram_overlap,
    ngrams,
    split_sentences,
    tokenize,
)
from ...models.channel import Channel, FrameworkType
from ...models.project import StageName
from ...models.script import (
    OriginalityDoc,
    PolicyCheckOutput,
    PolicyFindings,
    ScriptApproveEdits,
    ScriptDoc,
    ScriptDraft,
    ScriptParagraph,
    ScriptReviewPayload,
    ScriptSection,
    ScriptSentence,
    SpeechDoc,
    SpeechNormalizeOutput,
    SpeechSentence,
    TranscriptSummary,
)
from ...policy import gates
from ...storage import db
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult
from .title import read_json, read_title_doc

__all__ = [
    "ScriptStage",
    "count_words",
    "fingerprint",
    "ngram_overlap",
    "ngrams",
    "split_sentences",
    "tokenize",
]

log = logging.getLogger(__name__)

SCRIPT_JSON = "script.json"
SCRIPT_MD = "script.md"
SPEECH_JSON = "speech.json"
ORIGINALITY_JSON = "originality.json"
SUMMARY_JSON = "transcript_summary.json"
HISTORY_LIMIT = 30
PURPOSE_PREFIX = "_Purpose:"

CREATE_SCRIPT_HISTORY = """
CREATE TABLE IF NOT EXISTS script_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_slug TEXT NOT NULL,
    project_id TEXT,
    path TEXT NOT NULL,
    fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT ''
)
"""
SCRIPT_HISTORY_COLUMNS = {
    "channel_slug": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT",
    "path": "TEXT NOT NULL DEFAULT ''",
    "fingerprint": "TEXT",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}


# script_history -------------------------------------------------------------------------------


def ensure_script_history(app_data_dir: Path) -> None:
    db.ensure_table(app_data_dir, CREATE_SCRIPT_HISTORY)
    with db.connect(app_data_dir) as conn:
        add_missing_columns(conn, "script_history", SCRIPT_HISTORY_COLUMNS)


def recent_scripts(
    app_data_dir: Path,
    channel_slug: str,
    limit: int = HISTORY_LIMIT,
    exclude_project_id: str | None = None,
) -> list[dict[str, Any]]:
    ensure_script_history(app_data_dir)
    with db.connect(app_data_dir) as conn:
        rows = conn.execute(
            "SELECT project_id, path, fingerprint FROM script_history WHERE channel_slug = ? "
            "ORDER BY id DESC LIMIT ?",
            (channel_slug, limit + 1),
        ).fetchall()
    out = [
        dict(r) for r in rows
        if not (exclude_project_id and r["project_id"] == exclude_project_id)
    ]
    return out[:limit]


def upsert_script_history(
    app_data_dir: Path, channel_slug: str, project_id: str, path: Path, text_fingerprint: str
) -> None:
    ensure_script_history(app_data_dir)
    with db.connect(app_data_dir) as conn:
        conn.execute(
            "DELETE FROM script_history WHERE project_id = ? AND channel_slug = ?",
            (project_id, channel_slug),
        )
        conn.execute(
            "INSERT INTO script_history (channel_slug, project_id, path, fingerprint, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_slug, project_id, str(path), text_fingerprint, utc_now_iso()),
        )


def history_overlap(
    text: str, rows: list[dict[str, Any]], n: int
) -> tuple[float, int]:
    """Highest n-gram overlap with the scripts in ``rows`` whose files still exist."""
    own = fingerprint(text)
    best, compared = 0.0, 0
    for row in rows:
        if row.get("fingerprint") and row["fingerprint"] == own:
            return 1.0, compared + 1
        path = Path(str(row.get("path") or ""))
        try:
            other = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.suffix == ".json":
            try:
                other = ScriptDoc.model_validate_json(other).text()
            except (ValidationError, ValueError):
                continue
        compared += 1
        best = max(best, ngram_overlap(text, other, n))
    return round(best, 4), compared


# Pure maths and text --------------------------------------------------------------------------


def target_word_count(fmt: str, channel: Channel, wpm: int) -> tuple[int, str]:
    """Spoken words for the channel's target length, and a readable label of that length."""
    if fmt == "shorts":
        seconds = channel.channel.shorts_seconds
        return max(20, round(seconds / 60 * wpm)), f"{seconds} seconds"
    minutes = channel.channel.long_form_minutes
    return max(50, round(minutes * wpm)), f"{minutes} minutes"


def section_id(index: int) -> str:
    return f"sec-{index + 1:02d}"


def paragraph_id(section_index: int, paragraph_index: int) -> str:
    return f"p-{section_index + 1:02d}-{paragraph_index + 1:02d}"


def sentence_id(section_index: int, paragraph_index: int, sentence_index: int) -> str:
    return f"s-{section_index + 1:02d}-{paragraph_index + 1:02d}-{sentence_index + 1:02d}"


def make_paragraph(
    section_index: int, paragraph_index: int, text: str, locked: bool
) -> ScriptParagraph:
    clean = " ".join(text.split())
    sentences = [
        ScriptSentence(id=sentence_id(section_index, paragraph_index, i), text=s)
        for i, s in enumerate(split_sentences(clean))
    ]
    return ScriptParagraph(
        id=paragraph_id(section_index, paragraph_index), text=clean, sentences=sentences,
        locked=locked,
    )


@dataclass
class LockedParagraph:
    id: str
    section_index: int
    section_name: str
    paragraph_index: int
    text: str


def locked_paragraphs(
    doc: ScriptDoc | None, extra_ids: list[str] | None = None
) -> list[LockedParagraph]:
    """Paragraphs to keep verbatim: those flagged ``locked`` plus any ids passed in."""
    if doc is None:
        return []
    wanted = set(extra_ids or [])
    out: list[LockedParagraph] = []
    for si, section in enumerate(doc.sections):
        for pi, paragraph in enumerate(section.paragraphs):
            if paragraph.locked or paragraph.id in wanted:
                out.append(LockedParagraph(paragraph.id, si, section.name, pi, paragraph.text))
    return out


def build_sections(draft: ScriptDraft, locked: list[LockedParagraph]) -> list[ScriptSection]:
    """Sections with positional ids; locked paragraphs restored word for word."""
    by_id = {lock.id: lock for lock in locked}
    placed: set[str] = set()
    raw_sections: list[tuple[str, str, list[tuple[str, bool]]]] = []
    for section in draft.sections:
        paragraphs: list[tuple[str, bool]] = []
        for paragraph in section.paragraphs:
            lock = by_id.get(paragraph.locked_id or "")
            if lock is not None and lock.id not in placed:
                paragraphs.append((lock.text, True))
                placed.add(lock.id)
            elif paragraph.text.strip():
                paragraphs.append((paragraph.text, False))
        if paragraphs or section.name.strip():
            raw_sections.append(
                (section.name.strip() or "Section", section.purpose.strip(), paragraphs)
            )
    if not raw_sections:
        raise StageError("The model returned an empty script. Try again.")
    # Locked paragraphs the model forgot go back where they were (same section name, or index).
    for lock in locked:
        if lock.id in placed:
            continue
        target = next((s for s in raw_sections if s[0].lower() == lock.section_name.lower()), None)
        if target is None:
            target = raw_sections[min(lock.section_index, len(raw_sections) - 1)]
        position = min(lock.paragraph_index, len(target[2]))
        target[2].insert(position, (lock.text, True))
        placed.add(lock.id)
    sections: list[ScriptSection] = []
    for si, (name, purpose, paragraphs) in enumerate(raw_sections):
        sections.append(
            ScriptSection(
                id=section_id(si),
                name=name,
                purpose=purpose,
                paragraphs=[
                    make_paragraph(si, pi, text, is_locked)
                    for pi, (text, is_locked) in enumerate(paragraphs)
                ],
            )
        )
    return [s for s in sections if s.paragraphs] or sections


def script_to_markdown(doc: ScriptDoc) -> str:
    lines = [f"# {doc.title}", ""]
    for section in doc.sections:
        lines.append(f"## {section.name}")
        if section.purpose:
            lines.append(f"{PURPOSE_PREFIX} {section.purpose}_")
        lines.append("")
        for paragraph in section.paragraphs:
            lines.append(paragraph.text)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_script_markdown(markdown: str, previous: ScriptDoc | None = None) -> list[ScriptSection]:
    """``script.md`` back into sections. A paragraph keeps its lock while its text is
    unchanged; the ``locked_paragraph_ids`` edit sets the rest."""
    old_locked_text: set[str] = set()
    if previous is not None:
        for section in previous.sections:
            for paragraph in section.paragraphs:
                if paragraph.locked:
                    old_locked_text.add(" ".join(paragraph.text.split()))
    sections: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current is not None and buffer:
            text = " ".join(" ".join(buffer).split())
            if text:
                current[2].append(text)
        buffer = []

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.startswith("# ") and current is None:
            continue  # the title line
        if line.startswith("## "):
            flush()
            current = (line[3:].strip() or "Section", "", [])
            sections.append(current)
            continue
        if current is None:
            if line.strip():
                current = ("Script", "", [])
                sections.append(current)
            else:
                continue
        stripped = line.strip()
        if stripped.startswith(PURPOSE_PREFIX):
            purpose = stripped[len(PURPOSE_PREFIX):].strip().rstrip("_").strip()
            sections[-1] = (current[0], purpose, current[2])
            current = sections[-1]
            continue
        if not stripped:
            flush()
            continue
        buffer.append(stripped)
    flush()
    if not any(s[2] for s in sections):
        raise StageError("The edited script has no paragraphs.")
    result: list[ScriptSection] = []
    for si, (name, purpose, paragraphs) in enumerate(sections):
        if not paragraphs:
            continue
        result.append(
            ScriptSection(
                id=section_id(si),
                name=name,
                purpose=purpose,
                paragraphs=[
                    make_paragraph(si, pi, text, locked=text in old_locked_text)
                    for pi, text in enumerate(paragraphs)
                ],
            )
        )
    return result


def title_claim_early(title: str, text: str, share: float = 0.2) -> bool:
    """A cheap local check: a keyword of the title appears in the first ``share`` of the words."""
    from ...llm.text import keywords_of

    keys = [k.lower() for k in keywords_of(title)]
    if not keys:
        return True
    tokens = tokenize(text)
    head = set(tokens[: max(1, int(len(tokens) * share))])
    return any(k in head or any(t.startswith(k[:5]) for t in head) for k in keys)


# Files ----------------------------------------------------------------------------------------


def read_script_doc(stage_dir: Path) -> ScriptDoc | None:
    path = stage_dir / SCRIPT_JSON
    if not path.is_file():
        return None
    try:
        return ScriptDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise StageError(f"The file {path} could not be read: {exc}") from exc


def read_model(path: Path, model: type) -> Any:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def write_model(path: Path, model: Any) -> Path:
    atomic_write_text(path, model.model_dump_json(indent=2) + "\n")
    return path


def transcript_text(folder: Path) -> str:
    data = read_json(folder / "01_research" / "transcript.json") or {}
    text = str(data.get("text") or "").strip()
    if not text:
        segments = data.get("segments") or []
        text = " ".join(
            str(s.get("text", "")).strip() for s in segments if isinstance(s, dict)
        ).strip()
    return re.sub(r"\s+", " ", text)


def summary_text(summary: TranscriptSummary | None) -> str:
    if summary is None:
        return "(no competitor video; this is the channel's own topic)"
    lines = [f"- {b.name} ({b.share_percent}%): {b.purpose}" for b in summary.beats]
    lines.append(f"Hook style: {summary.hook_style}")
    lines.append(f"Pacing: {summary.pacing}")
    if summary.devices:
        lines.append("Devices: " + ", ".join(summary.devices))
    lines.append(f"Ending: {summary.ending_style}")
    if summary.notes:
        lines.append(f"Notes: {summary.notes}")
    return "\n".join(lines)


def review_payload(
    doc: ScriptDoc, markdown: str, speech: SpeechDoc | None, originality: OriginalityDoc,
    summary: TranscriptSummary | None,
) -> dict[str, Any]:
    return ScriptReviewPayload(
        script=doc, script_md=markdown, speech=speech, originality=originality,
        transcript_summary=summary,
    ).model_dump(mode="json")


def _llm(ctx: StageContext) -> Any:
    client = ctx.providers.get("llm")
    if client is None:
        raise StageError("No writing model is set up. Check Settings > Models and providers.")
    return client


def _rate_override(ctx: StageContext) -> int | None:
    """The speaking rate chosen in Settings, if any (``settings.voice.speaking_rate_wpm``)."""
    value = getattr(getattr(ctx.settings, "voice", None), "speaking_rate_wpm", None)
    return int(value) if isinstance(value, int | float) and value > 0 else None


def resolve_title(ctx: StageContext) -> str:
    title = (ctx.project.title or "").strip()
    if title:
        return title
    doc = read_title_doc(ctx.folder / "02_title")
    if doc is not None:
        if doc.chosen_title:
            return doc.chosen_title
        if doc.recommended_index is not None and doc.variant(doc.recommended_index):
            return doc.variant(doc.recommended_index).title  # type: ignore[union-attr]
    topic = (ctx.project.source.topic_text or "").strip()
    if topic:
        return topic
    raise StageError(
        "There is no approved title to write the script for. Finish the title step first."
    )


# The stage ----------------------------------------------------------------------------------


class ScriptStage:
    name = StageName.script

    async def run(self, ctx: StageContext) -> StageResult:
        llm = _llm(ctx)
        project, channel = ctx.project, ctx.channel
        stage_dir = ctx.stage_dir(StageName.script)
        title = resolve_title(ctx)
        fmt = project.format
        language = project.language
        wpm = speaking_rate_wpm(language, _rate_override(ctx))
        target, target_label = target_word_count(fmt, channel, wpm)
        cost = 0.0

        prompt = load_prompt("script")
        if fmt == "shorts":
            types: tuple[FrameworkType, ...] = ("script_shorts", "script_long")
        else:
            types = ("script_long", "script_shorts")
        framework = framework_text(
            channel, types, fmt, ctx.settings.resolved_shared_dir, prompt.default_framework,
            cache_dir=framework_cache_dir(ctx.settings.app_data_dir),
        )
        notes = [n for n in ctx.notes if n.strip()]
        if framework.note:
            notes.append(framework.note)

        # 1. Structure-only summary of the competitor transcript (cached in the stage folder).
        await ctx.report("Reading the competitor transcript", 5)
        source_text = transcript_text(ctx.folder)
        summary: TranscriptSummary | None = read_model(stage_dir / SUMMARY_JSON, TranscriptSummary)
        if source_text and summary is None:
            await ctx.report("Summarising the structure of the competitor video", 10)
            summary_prompt = load_prompt("transcript_summary")
            variables = {
                "language": language,
                "duration_note": f"About {count_words(source_text)} words of transcript.",
                "transcript_text": source_text,
            }
            summary, usage = await llm.complete(
                "transcript_summary", summary_prompt.render("system", variables),
                summary_prompt.render("user", variables), TranscriptSummary,
                variables=variables, project_id=project.id, stage=StageName.script.value,
            )
            cost += usage.cost_usd
            write_model(stage_dir / SUMMARY_JSON, summary)

        # 2. The script itself, keeping locked paragraphs.
        previous = read_script_doc(stage_dir)
        locked = locked_paragraphs(previous, ctx.edits.get("locked_paragraph_ids"))
        variables = {
            "title": title,
            "format": (
                "Shorts (vertical, under 3 minutes)" if fmt == "shorts" else "long-form video"
            ),
            "language": language,
            "target_length": target_label,
            "target_words": target,
            "niche": channel.channel.niche or "(not set)",
            "audience": channel.channel.audience or "(not set)",
            "brand_notes": channel.voice.style or "(none)",
            "transcript_summary": summary_text(summary),
            "locked_paragraphs": [
                f"[locked_id={lock.id}, section '{lock.section_name}'] {lock.text}"
                for lock in locked
            ] or ["(none)"],
            "notes": notes or ["(none)"],
            # for the mock only
            "locked_paragraphs_data": [
                {"id": lock.id, "section": lock.section_name, "text": lock.text} for lock in locked
            ],
        }
        mock_variables = {**variables, "locked_paragraphs": variables["locked_paragraphs_data"]}
        await ctx.report(f"Writing the script (about {target} words)", 20)
        draft, usage = await llm.complete(
            "script", [framework.text, prompt.render("system", variables)],
            prompt.render("user", variables), ScriptDraft, variables=mock_variables,
            project_id=project.id, stage=StageName.script.value,
        )
        cost += usage.cost_usd
        sections = build_sections(draft, locked)
        doc = ScriptDoc(
            title=title,
            language=language,
            format=fmt,
            target_words=target,
            sections=sections,
            word_count=sum(count_words(p.text) for s in sections for p in s.paragraphs),
            model=usage.model,
            generated_at=datetime.now(UTC),
            speaking_rate_wpm=wpm,
            framework_source=framework.source,
            framework_name=framework.framework.name if framework.framework else None,
            notes=notes,
        )
        markdown = script_to_markdown(doc)
        outputs = [
            write_model(stage_dir / SCRIPT_JSON, doc),
            _write_text(stage_dir / SCRIPT_MD, markdown),
        ]

        # 3. Spoken form.
        await ctx.report("Writing out numbers and abbreviations for the voice", 60)
        speech, speech_cost = await normalise_speech(llm, doc, None, project.id)
        cost += speech_cost
        outputs.append(write_model(stage_dir / SPEECH_JSON, speech))

        # 4. Policy reading and originality maths.
        await ctx.report("Checking originality and policy", 75)
        policy, policy_cost = await policy_check(llm, doc, summary, project.id)
        cost += policy_cost
        originality = measure_originality(
            doc, source_text, ctx.settings.app_data_dir, project.channel_slug, project.id, policy
        )
        outputs.append(write_model(stage_dir / ORIGINALITY_JSON, originality))
        upsert_script_history(
            ctx.settings.app_data_dir, project.channel_slug, project.id,
            stage_dir / SCRIPT_MD, fingerprint(doc.text()),
        )
        if originality.blocking_reasons:
            raise GateBlocked(originality.blocking_reasons, cost_usd=round(cost, 6))
        await ctx.report("Script ready for review", 100)
        return StageResult(
            outputs=outputs,
            summary=f"{doc.word_count} words in {len(doc.sections)} sections "
            f"(target {target}); originality {originality.ngram_overlap_source * 100:.1f}% "
            "overlap with the source.",
            cost_usd=round(cost, 6),
            needs_review_payload=review_payload(doc, markdown, speech, originality, summary),
            gate_results=originality.gate_results,
        )

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits ``{script_md?, locked_paragraph_ids?}``: replace the text, set locks.

        Everything that can fail (the spoken-form model call, the originality maths) happens
        before any file is touched, so a model error leaves ``03_script`` exactly as it was;
        the four files are then written together. The gate results go back to the engine,
        which refuses the approval when a blocking check fails (unless overridden).
        """
        try:
            edits = ScriptApproveEdits.model_validate(ctx.edits or {})
        except ValidationError as exc:
            raise StageError(f"The script edits are not valid: {exc.errors()[0]['msg']}") from exc
        if edits.script_md is None and edits.locked_paragraph_ids is None:
            return None
        stage_dir = ctx.stage_dir(StageName.script)
        doc = read_script_doc(stage_dir)
        if doc is None:
            raise StageError("The script step has not produced a script yet.")
        changed_text = False
        if edits.script_md is not None and edits.script_md.strip():
            doc.sections = parse_script_markdown(edits.script_md, doc)
            changed_text = True
        if edits.locked_paragraph_ids is not None:
            wanted = set(edits.locked_paragraph_ids)
            for paragraph in doc.paragraphs():
                paragraph.locked = paragraph.id in wanted
        doc.word_count = sum(count_words(p.text) for p in doc.paragraphs())
        markdown = script_to_markdown(doc)
        speech = read_model(stage_dir / SPEECH_JSON, SpeechDoc)
        summary = read_model(stage_dir / SUMMARY_JSON, TranscriptSummary)
        originality = read_model(stage_dir / ORIGINALITY_JSON, OriginalityDoc)
        cost = 0.0
        if changed_text:
            # 1. Compute first (this is the step that talks to the model) ...
            speech, cost = await normalise_speech(_llm(ctx), doc, speech, ctx.project.id)
            previous_policy = originality.policy if originality else PolicyFindings()
            findings = PolicyCheckOutput(
                advisory_persona=previous_policy.advisory_persona,
                sensitive_topic=previous_policy.sensitive_topic,
                title_claim_early=title_claim_early(doc.title, doc.text()),
                reasons=list(previous_policy.reasons),
                semantic_note=originality.semantic_note if originality else "",
            )
            originality = measure_originality(
                doc, transcript_text(ctx.folder), ctx.settings.app_data_dir,
                ctx.project.channel_slug, ctx.project.id, findings,
            )
        # 2. ... then write the files together.
        outputs = [
            write_model(stage_dir / SCRIPT_JSON, doc),
            _write_text(stage_dir / SCRIPT_MD, markdown),
        ]
        if changed_text:
            outputs.append(write_model(stage_dir / SPEECH_JSON, speech))
            outputs.append(write_model(stage_dir / ORIGINALITY_JSON, originality))
            upsert_script_history(
                ctx.settings.app_data_dir, ctx.project.channel_slug, ctx.project.id,
                stage_dir / SCRIPT_MD, fingerprint(doc.text()),
            )
        if originality is None:
            originality = OriginalityDoc(
                ngram_overlap_source=0.0, ngram_overlap_history_max=0.0, passed=True,
                word_count=doc.word_count, target_words=doc.target_words,
            )
        return StageResult(
            outputs=outputs,
            summary=f"Script edited by the reviewer: {doc.word_count} words.",
            cost_usd=round(cost, 6),
            needs_review_payload=review_payload(doc, markdown, speech, originality, summary),
            gate_results=originality.gate_results,
        )


def _write_text(path: Path, text: str) -> Path:
    atomic_write_text(path, text)
    return path


async def normalise_speech(
    llm: Any, doc: ScriptDoc, previous: SpeechDoc | None, project_id: str
) -> tuple[SpeechDoc, float]:
    """``speech.json`` for every sentence; unchanged sentences keep their earlier spoken form."""
    known: dict[str, str] = {}
    if previous is not None:
        known = {s.text: s.speech_text for s in previous.sentences if s.speech_text}
    sentences = doc.sentences()
    todo = [s for s in sentences if s.text not in known]
    spoken: dict[str, str] = {s.id: known[s.text] for s in sentences if s.text in known}
    cost = 0.0
    model = previous.model if previous else "none"
    if todo:
        prompt = load_prompt("speech_normalize")
        rows = [{"id": s.id, "text": s.text} for s in todo]
        variables = {
            "language": doc.language,
            "sentences_json": json.dumps(rows, ensure_ascii=False, indent=1),
            "sentences": rows,
        }
        output, usage = await llm.complete(
            "speech_normalize", prompt.render("system", variables),
            prompt.render("user", variables), SpeechNormalizeOutput, variables=variables,
            project_id=project_id, stage=StageName.script.value,
        )
        cost = usage.cost_usd
        model = usage.model
        for row in output.sentences:
            if row.speech_text.strip():
                spoken[row.id] = " ".join(row.speech_text.split())
    speech = SpeechDoc(
        language=doc.language,
        sentences=[
            SpeechSentence(id=s.id, text=s.text, speech_text=spoken.get(s.id) or s.text)
            for s in sentences
        ],
        model=model,
        generated_at=datetime.now(UTC),
    )
    return speech, cost


async def policy_check(
    llm: Any, doc: ScriptDoc, summary: TranscriptSummary | None, project_id: str
) -> tuple[PolicyCheckOutput, float]:
    prompt = load_prompt("policy_check")
    variables = {
        "title": doc.title,
        "language": doc.language,
        "source_summary": summary_text(summary),
        "script_text": doc.text(),
    }
    output, usage = await llm.complete(
        "policy_check", prompt.render("system", variables), prompt.render("user", variables),
        PolicyCheckOutput, variables=variables, project_id=project_id,
        stage=StageName.script.value,
    )
    return output, usage.cost_usd


def measure_originality(
    doc: ScriptDoc,
    source_text: str,
    app_data_dir: Path,
    channel_slug: str,
    project_id: str,
    policy: PolicyCheckOutput,
) -> OriginalityDoc:
    """The 8-gram maths plus the policy reading, judged by the script gates."""
    text = doc.text()
    n_source = int(gates.params("script.ngram_source").get("n", 8))
    n_history = int(gates.params("script.ngram_history").get("n", 8))
    history_size = int(gates.params("script.ngram_history").get("history_size", HISTORY_LIMIT))
    overlap_source = ngram_overlap(text, source_text, n_source) if source_text else 0.0
    rows = recent_scripts(app_data_dir, channel_slug, history_size, exclude_project_id=project_id)
    overlap_history, compared = history_overlap(text, rows, n_history)
    context = {
        "ngram_overlap_source": overlap_source,
        "ngram_overlap_history_max": overlap_history,
        "source_compared": bool(source_text),
        "history_compared": compared,
        "advisory_persona": policy.advisory_persona,
        "sensitive_topic": policy.sensitive_topic,
        "word_count": doc.word_count,
        "target_words": doc.target_words,
        "title_claim_early": policy.title_claim_early,
        "reasons": list(policy.reasons),
    }
    results = gates.evaluate("script", context)
    blocking = gates.blocking_reasons(results)
    return OriginalityDoc(
        ngram_overlap_source=overlap_source,
        ngram_overlap_history_max=overlap_history,
        semantic_note=policy.semantic_note,
        policy=PolicyFindings(
            advisory_persona=policy.advisory_persona,
            sensitive_topic=policy.sensitive_topic,
            reasons=list(policy.reasons),
        ),
        passed=not blocking,
        word_count=doc.word_count,
        target_words=doc.target_words,
        title_claim_early=policy.title_claim_early,
        history_compared=compared,
        source_compared=bool(source_text),
        gate_results=gates.to_dicts(results),
        blocking_reasons=blocking,
    )


def load_review_payload(folder: Path) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    stage_dir = folder / "03_script"
    doc = read_script_doc(stage_dir)
    if doc is None:
        return {}
    originality = read_model(stage_dir / ORIGINALITY_JSON, OriginalityDoc) or OriginalityDoc(
        ngram_overlap_source=0.0, ngram_overlap_history_max=0.0, passed=True
    )
    try:
        markdown = (stage_dir / SCRIPT_MD).read_text(encoding="utf-8")
    except OSError:
        markdown = script_to_markdown(doc)
    return review_payload(
        doc, markdown, read_model(stage_dir / SPEECH_JSON, SpeechDoc), originality,
        read_model(stage_dir / SUMMARY_JSON, TranscriptSummary),
    )
