"""The provenance bundle (``08_export/provenance.json`` and ``provenance.md``) and the
synthetic-media disclosure (docs/M3-M4-CONTRACT.md section 4).

Everything here reads what the earlier stages left in the project folder, loosely: the
research pick, the title options, the script, the storyboard prompts, ``06_images/images.json``
(provider, model, seed, SynthID / C2PA flags, QA verdicts), the voice files,
``07_edit/timeline.json`` (presets, music licence) and the review log in ``job.json``. A
file that is missing or has another shape simply leaves its section short; the export never
fails over provenance.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models.channel import Channel
from ..models.export import (
    Disclosure,
    ImageProvenance,
    LicenceRecord,
    ProvenanceDoc,
    ProvenanceSummary,
    VoiceProvenance,
)
from ..models.project import Project

FONT_LICENCE = "SIL Open Font License 1.1"
VOICE_RECORD_FILES = ("voice.json", "synthesis.json", "provenance.json", "review_payload.json")
VOICE_CONSENT_FILES = ("consent.json",)


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_dict(path: Path) -> dict[str, Any]:
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "0"):
        return False
    return None


def _scene_number(row: dict[str, Any], fallback: int) -> int:
    for key in ("scene", "index", "scene_index"):
        value = row.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return fallback


def _image_rows(folder: Path) -> list[dict[str, Any]]:
    """Scene rows from ``images.json`` (whatever list it keeps them in), else the storyboard."""
    data = read_json(folder / "06_images" / "images.json")
    if isinstance(data, dict):
        for key in ("scenes", "images", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    elif isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    storyboard = read_dict(folder / "04_storyboard" / "storyboard.json")
    rows = []
    for scene in storyboard.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        image = scene.get("image") if isinstance(scene.get("image"), dict) else {}
        rows.append(
            {
                "scene": scene.get("index"),
                "path": image.get("path"),
                "status": image.get("status"),
                "qa": image.get("qa"),
                "prompt": scene.get("image_prompt"),
            }
        )
    return rows


def image_provenance(folder: Path, channel: Channel) -> list[ImageProvenance]:
    """Provider, model and watermark flags per scene for the disclosure."""
    default_provider = os.environ.get("CCS_IMAGE_PROVIDER", "").strip() or channel.images.tool
    out: list[ImageProvenance] = []
    for index, row in enumerate(_image_rows(folder)):
        flags = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        out.append(
            ImageProvenance(
                scene=_scene_number(row, index),
                provider=str(row.get("provider") or result.get("provider") or default_provider),
                model=str(row.get("model") or result.get("model") or channel.images.model or ""),
                synthid=_flag(flags.get("synthid", row.get("synthid"))),
                c2pa=_flag(flags.get("c2pa", row.get("c2pa"))),
                path=str(row.get("path") or result.get("path") or ""),
            )
        )
    return out


def qa_verdicts(folder: Path) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    for index, row in enumerate(_image_rows(folder)):
        qa = row.get("qa")
        if isinstance(qa, dict) and qa:
            verdicts.append({"scene": _scene_number(row, index), **qa})
    return verdicts


def scene_prompts(folder: Path) -> list[dict[str, Any]]:
    storyboard = read_dict(folder / "04_storyboard" / "storyboard.json")
    prompts: list[dict[str, Any]] = []
    for scene in storyboard.get("scenes") or []:
        if isinstance(scene, dict) and scene.get("image_prompt"):
            prompts.append(
                {
                    "scene": scene.get("index"),
                    "image_prompt": scene.get("image_prompt"),
                    "negative_prompt": scene.get("negative_prompt", ""),
                }
            )
    return prompts


def voice_provenance(folder: Path, channel: Channel) -> VoiceProvenance:
    """Provider, model and consent reference of the narration.

    The reference names a record that was actually found (``voice.json``, the channel's
    Consent fields, or a ``consent.json``); a cloned voice without one says so plainly
    instead of claiming consent is on file somewhere."""
    provider = channel.voice.tool
    model = channel.voice.model or ""
    consent_ref = ""
    voice_dir = folder / "05_voice"
    for name in VOICE_RECORD_FILES:
        data = read_dict(voice_dir / name)
        if not data:
            continue
        provider = str(data.get("provider") or provider)
        model = str(data.get("model") or model)
        consent = data.get("consent_ref") or data.get("consent")
        if isinstance(consent, dict):
            consent = consent.get("path") or consent.get("ref") or json.dumps(consent)[:200]
        if consent:
            consent_ref = str(consent)
        break
    if not consent_ref:
        candidates = [voice_dir / n for n in VOICE_CONSENT_FILES]
        sample = (channel.voice.sample_path or "").strip()
        if sample:
            candidates.append(Path(sample).with_name("consent.json"))
        for path in candidates:
            if path.is_file():
                consent_ref = str(path)
                break
    inline = getattr(channel.voice, "consent", None)
    if not consent_ref and inline is not None and getattr(inline, "is_filled", False):
        consent_ref = (
            f"the channel's voice settings (owner {inline.owner_name}, recorded by "
            f"{inline.consented_by or 'unknown'})"
        )
    if not consent_ref and (channel.voice.clone_ref or channel.voice.sample_path):
        consent_ref = "none recorded"
    return VoiceProvenance(provider=provider, model=model, consent_ref=consent_ref)


def licences(folder: Path, timeline: dict[str, Any] | None) -> list[LicenceRecord]:
    records: list[LicenceRecord] = []
    music = (timeline or {}).get("music") if isinstance((timeline or {}).get("music"), dict) else {}
    if music and music.get("path"):
        path = Path(str(music["path"]))
        sidecar = path.with_name(path.name + ".license.txt")
        text = ""
        if sidecar.is_file():
            try:
                text = sidecar.read_text(encoding="utf-8", errors="replace")[:300].strip()
            except OSError:
                text = ""
        records.append(
            LicenceRecord(
                item=f"music: {path.name}",
                licence=text or (
                    "licence file present" if music.get("license_ok") else "none found"
                ),
                path=str(path),
                ok=_flag(music.get("license_ok")),
            )
        )
    fonts_dir = Path(__file__).resolve().parents[3] / "assets" / "fonts"
    licence_file = (
        next(iter(sorted(fonts_dir.glob("*LICENSE*"))), None) if fonts_dir.is_dir() else None
    )
    records.append(
        LicenceRecord(
            item="fonts: Noto (captions, popups, thumbnail headline)",
            licence=FONT_LICENCE,
            path=str(licence_file) if licence_file else "",
            ok=True if licence_file else None,
        )
    )
    return records


def review_log(project: Project) -> dict[str, list[str]]:
    return {stage.value: list(state.history) for stage, state in project.stages.items()}


def disclosure_for(folder: Path, channel: Channel, altered_or_synthetic: bool = True) -> Disclosure:
    return Disclosure(
        altered_or_synthetic=altered_or_synthetic,
        provenance=ProvenanceSummary(
            images=image_provenance(folder, channel), voice=voice_provenance(folder, channel)
        ),
    )


def build_provenance(
    *,
    project: Project,
    channel: Channel,
    folder: Path,
    disclosure: Disclosure,
    thumbnail: dict[str, Any],
    render: dict[str, Any],
    seo: dict[str, Any],
    costs: dict[str, float],
    timeline: dict[str, Any] | None,
) -> ProvenanceDoc:
    research_dir = folder / "01_research"
    pick = read_dict(research_dir / "pick.json")
    video = read_dict(research_dir / "video.json")
    candidate = pick.get("candidate") if isinstance(pick.get("candidate"), dict) else {}
    title_doc = read_dict(folder / "02_title" / "title.json")
    script = read_dict(folder / "03_script" / "script.json")
    originality = read_dict(folder / "03_script" / "originality.json")
    storyboard = read_dict(folder / "04_storyboard" / "storyboard.json")
    script_versions = sorted(
        p.name for p in (folder / "03_script").glob("script*.json")
    ) if (folder / "03_script").is_dir() else []
    return ProvenanceDoc(
        project_id=project.id,
        channel_slug=project.channel_slug,
        title=project.title,
        language=project.language,
        format=project.format,
        generated_at=datetime.now(UTC),
        research={
            "source": project.source.model_dump(mode="json"),
            "pick": {
                "kind": pick.get("kind"),
                "video_id": pick.get("video_id"),
                "url": pick.get("url"),
                "title": pick.get("title"),
                "channel_name": pick.get("channel_name"),
                "strategy": pick.get("strategy"),
                "reason": pick.get("reason"),
                "views": candidate.get("views", video.get("view_count")),
                "outlier_score": candidate.get("outlier_score"),
            } if pick else {},
            "transcript_used": (research_dir / "transcript.json").is_file(),
            "competitor_thumbnail": (research_dir / "competitor_thumbnail.jpg").is_file(),
        },
        title_variants=[
            {
                "index": v.get("index"),
                "title": v.get("title"),
                "viral_score": v.get("viral_score"),
                "flags": v.get("flags", []),
                "chosen": v.get("index") == title_doc.get("chosen_index"),
            }
            for v in title_doc.get("variants") or []
            if isinstance(v, dict)
        ],
        script={
            "model": script.get("model"),
            "generated_at": script.get("generated_at"),
            "word_count": script.get("word_count"),
            "target_words": script.get("target_words"),
            "framework": script.get("framework_name") or script.get("framework_source"),
            "versions": script_versions,
            "originality": {
                "ngram_overlap_source": originality.get("ngram_overlap_source"),
                "ngram_overlap_history_max": originality.get("ngram_overlap_history_max"),
                "passed": originality.get("passed"),
                "title_claim_early": originality.get("title_claim_early"),
            } if originality else {},
        },
        prompts={
            "files": {
                "title": "llm/prompts/title.md",
                "script": "llm/prompts/script.md",
                "speech_normalize": "llm/prompts/speech_normalize.md",
                "storyboard": "llm/prompts/storyboard.md",
                "policy_check": "llm/prompts/policy_check.md",
                "seo": "llm/prompts/seo.md",
                "thumbnail_template": "llm/prompts/thumbnail_template.md",
            },
            "storyboard_model": storyboard.get("model"),
            "style_guide": storyboard.get("style_guide") or channel.images.style_guide,
            "scenes": scene_prompts(folder),
            "thumbnail_subject": thumbnail.get("subject_prompt", ""),
        },
        qa=qa_verdicts(folder),
        images=disclosure.provenance.images,
        voice=disclosure.provenance.voice,
        render=render,
        thumbnail={k: v for k, v in thumbnail.items() if k != "subject_prompt"},
        disclosure=disclosure,
        review_log=review_log(project),
        licences=licences(folder, timeline),
        costs=costs,
    )


def _md_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def provenance_markdown(doc: ProvenanceDoc) -> str:
    """A readable version of the bundle for the person uploading the video."""
    lines: list[str] = [
        f"# Provenance: {doc.title}",
        "",
        f"Project {doc.project_id} on channel {doc.channel_slug}, {doc.format}, "
        f"{doc.language}. Generated {doc.generated_at.strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "## Disclosure",
        "",
        f"- Altered or synthetic content: {_md_value(doc.disclosure.altered_or_synthetic)}",
        f"- Voice: {_md_value(doc.voice.provider)} {_md_value(doc.voice.model)} "
        f"(consent: {_md_value(doc.voice.consent_ref)})",
        f"- Pictures: {len(doc.images)} generated",
        "",
        "## Research",
        "",
    ]
    pick = doc.research.get("pick") or {}
    if pick:
        lines.append(
            f"- Picked video: {_md_value(pick.get('title'))} by "
            f"{_md_value(pick.get('channel_name'))} ({_md_value(pick.get('url'))})"
        )
        lines.append(
            f"- Views {_md_value(pick.get('views'))}, outlier score "
            f"{_md_value(pick.get('outlier_score'))}, strategy {_md_value(pick.get('strategy'))}"
        )
        if pick.get("reason"):
            lines.append(f"- Why: {pick['reason']}")
    else:
        source = doc.research.get("source") or {}
        lines.append(f"- Own topic: {_md_value(source.get('topic_text'))}")
    lines += ["", "## Title options", ""]
    for variant in doc.title_variants:
        mark = " (chosen)" if variant.get("chosen") else ""
        flags = f" flags: {', '.join(variant['flags'])}" if variant.get("flags") else ""
        lines.append(
            f"- {variant.get('index')}: {variant.get('title')} "
            f"(score {_md_value(variant.get('viral_score'))}){mark}{flags}"
        )
    if not doc.title_variants:
        lines.append("- (none recorded)")
    lines += ["", "## Script", ""]
    for key in ("model", "generated_at", "word_count", "target_words", "framework", "versions"):
        lines.append(f"- {key.replace('_', ' ')}: {_md_value(doc.script.get(key))}")
    originality = doc.script.get("originality") or {}
    if originality:
        lines.append(
            f"- originality: source overlap {_md_value(originality.get('ngram_overlap_source'))}, "
            f"history overlap {_md_value(originality.get('ngram_overlap_history_max'))}, "
            f"passed {_md_value(originality.get('passed'))}"
        )
    lines += ["", "## Pictures", ""]
    qa_by_scene = {q.get("scene"): q for q in doc.qa}
    for image in doc.images:
        verdict = qa_by_scene.get(image.scene) or {}
        score = verdict.get("score")
        lines.append(
            f"- Scene {image.scene}: {_md_value(image.provider)} {_md_value(image.model)}, "
            f"SynthID {_md_value(image.synthid)}, C2PA {_md_value(image.c2pa)}"
            + (f", QA score {score}" if score is not None else "")
        )
    if not doc.images:
        lines.append("- (none recorded)")
    lines += ["", "## Prompts", ""]
    files = doc.prompts.get("files") or {}
    lines.append("- Prompt files: " + ", ".join(f"{k} ({v})" for k, v in files.items()))
    if doc.prompts.get("style_guide"):
        lines.append(f"- Style guide: {doc.prompts['style_guide']}")
    for scene in doc.prompts.get("scenes") or []:
        lines.append(f"- Scene {scene.get('scene')}: {scene.get('image_prompt')}")
    if doc.prompts.get("thumbnail_subject"):
        lines.append(f"- Thumbnail picture: {doc.prompts['thumbnail_subject']}")
    lines += ["", "## Thumbnail", ""]
    for key, value in doc.thumbnail.items():
        lines.append(f"- {key.replace('_', ' ')}: {_md_value(value)}")
    lines += ["", "## Render and export", ""]
    for key, value in doc.render.items():
        lines.append(f"- {key.replace('_', ' ')}: {_md_value(value)}")
    lines += ["", "## Licences", ""]
    for record in doc.licences:
        lines.append(
            f"- {record.item}: {_md_value(record.licence)} "
            f"({_md_value(record.path)}) ok: {_md_value(record.ok)}"
        )
    lines += ["", "## Costs", ""]
    for key, value in doc.costs.items():
        lines.append(f"- {key}: ${value:.4f}")
    lines += ["", "## Review log", ""]
    for stage, entries in doc.review_log.items():
        if not entries:
            continue
        lines.append(f"### {stage}")
        lines.append("")
        lines.extend(f"- {entry}" for entry in entries)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
