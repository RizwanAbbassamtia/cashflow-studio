"""Export stage: thumbnails, the SEO pack, the disclosure and provenance bundle, and the
copy into the export folder (docs/M3-M4-CONTRACT.md section 4).

Reads the final renders in ``07_edit/``, the approved title, ``03_script/script.json``,
``05_voice/timing.json`` (the clock for the chapters), ``01_research/competitor_thumbnail.jpg``
and ``pick.json``, the channel's thumbnail config, brand colours and language. Writes into
``08_export/``: the text-free subject pictures, three thumbnail variants in 16:9 and 9:16,
``metadata.json``, ``provenance.json`` / ``provenance.md`` and ``export.json`` (what was
done, so edits and a restart build on it).

Approve = export (docs/M3-M4-CONTRACT.md section 4): the videos, the chosen thumbnails and
the documents are copied into ``<export folder>/<channel>/<date>_<topic>/`` by the
``on_approve`` hook, which the engine calls when a person approves the stage, so nothing an
uploader could publish leaves the project folder before the review. When the stage is set
to ``auto`` (nobody reviews it) the run itself copies the files. ``export.json`` carries
``exported`` so the review screen can tell "ready" from "in the export folder".

Gates: ``export.thumbnail_similarity`` (pHash distance to the competitor thumbnail above 12,
block), ``export.title_promise`` (the title's claim is made early, warn) and
``export.files_present`` (a selected preset has no rendered file, block). The pack is
written before a gate blocks, so the reviewer sees everything and can override.

Approval edits (``apply_edits``): ``{metadata: {...}}`` changes the SEO fields,
``{thumbnail_choice: "v1"|"v2"|"v3"}`` picks the thumbnail that is exported,
``{altered_or_synthetic: bool}`` sets the disclosure flag, ``{headline: str}`` re-renders the
chosen thumbnail's text only (no new picture). The same keys on a redo shape the new run.
File copies and the Pillow work run in a worker thread that is waited for on a cancel.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...export import exporter
from ...export import seo as seo_module
from ...export import thumbnail as thumbs
from ...export.provenance import (
    build_provenance,
    disclosure_for,
    provenance_markdown,
    read_dict,
)
from ...models.export import (
    VARIANT_IDS,
    Chapter,
    ExportApproveEdits,
    ExportDoc,
    ExportedFile,
    ExportMetadata,
    ExportReviewPayload,
    SeoPack,
    ThumbnailTemplate,
    ThumbnailView,
)
from ...models.project import Project, StageName
from ...models.script import ScriptDoc
from ...policy import gates
from ...providers.base import ProviderError
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult, run_in_thread
from .script import read_script_doc

log = logging.getLogger(__name__)

REVIEW_PAYLOAD_FILE = "review_payload.json"
METADATA_KEYS = ("title", "description", "tags", "chapters", "pinned_comment", "hashtags")
EDIT_KEYS = ("metadata", "thumbnail_choice", "altered_or_synthetic", "headline")
CANCELLED_MESSAGE = "Stopped: the project was archived while the export was running."


def utc_now() -> datetime:
    return datetime.now(UTC)


# Files -----------------------------------------------------------------------------------------


def read_export_doc(stage_dir: Path) -> ExportDoc | None:
    path = stage_dir / exporter.EXPORT_FILE
    if not path.is_file():
        return None
    try:
        return ExportDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise StageError(f"The file {path} could not be read: {exc}") from exc


def read_metadata(stage_dir: Path) -> ExportMetadata | None:
    path = stage_dir / exporter.METADATA_FILE
    if not path.is_file():
        return None
    try:
        return ExportMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise StageError(f"The file {path} could not be read: {exc}") from exc


def write_json_model(path: Path, model: Any) -> Path:
    atomic_write_text(path, model.model_dump_json(indent=2) + "\n")
    return path


def file_url(project_id: str, name: str) -> str:
    return f"/api/projects/{project_id}/files/08_export/{name}"


def review_payload(
    project_id: str, doc: ExportDoc, metadata: ExportMetadata, max_headline_words: int
) -> dict[str, Any]:
    thumbnails = [
        ThumbnailView(
            id=v.id,
            headline=v.headline,
            url=file_url(project_id, v.file),
            url_shorts=file_url(project_id, v.file_shorts),
            file=v.file,
            file_shorts=v.file_shorts,
            distance=v.distance,
        )
        for v in doc.thumbnails
    ]
    return ExportReviewPayload(
        thumbnails=thumbnails,
        thumbnail_choice=doc.thumbnail_choice,
        max_headline_words=max_headline_words,
        metadata=metadata,
        disclosure=metadata.disclosure,
        export_folder=doc.export_folder,
        exported=doc.exported,
        files=doc.files,
        presets=doc.presets,
        missing_presets=doc.missing_presets,
        provenance_url=file_url(project_id, exporter.PROVENANCE_JSON),
        provenance_md_url=file_url(project_id, exporter.PROVENANCE_MD),
        template_source=doc.template_source,
        title_promise_early=doc.title_promise_early,
        title_promise_note=doc.title_promise_note,
        warnings=doc.warnings,
        gate_results=doc.gate_results,
        costs=doc.costs,
    ).model_dump(mode="json")


def load_review_payload(
    folder: Path, project_id: str, max_headline_words: int = 4
) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    stage_dir = Path(folder) / "08_export"
    doc = read_export_doc(stage_dir)
    metadata = read_metadata(stage_dir)
    if doc is None or metadata is None:
        return {}
    return review_payload(project_id, doc, metadata, max_headline_words)


# Pieces of a run -------------------------------------------------------------------------------


def _check_cancel(ctx: StageContext) -> None:
    if ctx.cancelled:
        raise StageError(CANCELLED_MESSAGE)


def _seed(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def gate_context(doc: ExportDoc) -> dict[str, Any]:
    chosen = doc.chosen()
    return {
        "thumbnail_distance": chosen.distance if chosen else None,
        "competitor_thumbnail": bool(doc.competitor_thumbnail),
        "title_promise_early": doc.title_promise_early,
        "title_promise_note": doc.title_promise_note,
        "presets": list(doc.presets),
        "missing_presets": list(doc.missing_presets),
    }


def apply_metadata_edits(
    metadata: ExportMetadata, edits: dict[str, Any], notes: list[str]
) -> ExportMetadata:
    """``{metadata: {title, description, tags, chapters, pinned_comment, hashtags}}``."""
    data = metadata.model_dump()
    applied: list[str] = []
    for key in METADATA_KEYS:
        if key not in edits:
            continue
        value = edits[key]
        if key in ("title", "description", "pinned_comment"):
            if not isinstance(value, str):
                raise StageError(f"The metadata field '{key}' must be text.")
            if key == "title" and not value.strip():
                raise StageError("The title cannot be empty.")
            data[key] = value.strip()
        elif key in ("tags", "hashtags"):
            if isinstance(value, str):
                value = [v for v in value.replace("\n", ",").split(",")]
            if not isinstance(value, list):
                raise StageError(f"The metadata field '{key}' must be a list of words.")
            data[key] = [str(v).strip() for v in value if str(v).strip()]
        elif key == "chapters":
            if not isinstance(value, list):
                raise StageError("Chapters must be a list of {time, title} entries.")
            rows: list[Chapter] = []
            for row in value:
                if isinstance(row, dict) and row.get("time") is not None:
                    rows.append(Chapter(
                        time=str(row["time"]).strip(), title=str(row.get("title") or "").strip()
                    ))
                elif isinstance(row, str) and " " in row.strip():
                    time, _, title = row.strip().partition(" ")
                    rows.append(Chapter(time=time, title=title.strip()))
            data[key] = [r.model_dump() for r in rows]
        applied.append(key)
    unknown = sorted(set(edits) - set(METADATA_KEYS))
    if unknown:
        notes.append(f"Ignored unknown metadata fields: {', '.join(unknown)}.")
    updated = ExportMetadata.model_validate(data)
    pack = seo_module.enforce_limits(updated.seo(), notes)
    updated.title = pack.title
    updated.description = pack.description
    updated.tags = pack.tags
    updated.chapters = pack.chapters
    updated.pinned_comment = pack.pinned_comment
    updated.hashtags = pack.hashtags
    if applied:
        notes.append(f"Metadata changed by the reviewer: {', '.join(applied)}.")
    return updated


def _timeline_summary(timeline: dict[str, Any] | None) -> dict[str, Any]:
    timeline = timeline or {}
    music = timeline.get("music") if isinstance(timeline.get("music"), dict) else {}
    captions = timeline.get("captions") if isinstance(timeline.get("captions"), dict) else {}
    return {
        "duration_s": timeline.get("duration_s"),
        "size": f"{timeline.get('width')}x{timeline.get('height')}"
        if timeline.get("width") and timeline.get("height") else None,
        "fps": timeline.get("fps"),
        "scenes": len(timeline.get("scenes") or []) if isinstance(timeline.get("scenes"), list)
        else None,
        "music": Path(str(music["path"])).name if music.get("path") else None,
        "music_license_ok": music.get("license_ok"),
        "captions": captions.get("enabled"),
    }


class ExportStage:
    name = StageName.export

    # The run ------------------------------------------------------------------------------

    async def run(self, ctx: StageContext) -> StageResult:
        project, channel = ctx.project, ctx.channel
        folder = ctx.folder
        out = ctx.stage_dir(StageName.export)
        warnings: list[str] = []
        llm = ctx.providers.get("llm")
        try:
            edits = ExportApproveEdits.model_validate(
                {k: v for k, v in (ctx.edits or {}).items() if k in EDIT_KEYS}
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise StageError(f"The export edits are not valid ({where}): {first['msg']}") from exc

        await ctx.report("Reading the script and the voice clock", 5)
        script = read_script_doc(folder / "03_script")
        if script is None:
            raise StageError("The script step has not produced a script yet. Finish it first.")
        timing = read_dict(folder / "05_voice" / "timing.json") or None
        if timing is None:
            warnings.append(
                "No voice timing file (05_voice/timing.json): chapter times are estimated "
                "from the word count."
            )
        timeline = read_dict(folder / "07_edit" / "timeline.json") or None
        title = " ".join(project.title.split()) or script.title
        max_words = channel.thumbnail.max_headline_words
        notes = [n for n in ctx.notes if n.strip()]

        # 1. The thumbnail layout from the competitor thumbnail (cached per video).
        _check_cancel(ctx)
        await ctx.report("Reading the competitor thumbnail layout", 12)
        pick = read_dict(folder / "01_research" / "pick.json")
        video_id = str(pick.get("video_id") or project.source.video_id or "").strip() or None
        competitor = folder / "01_research" / "competitor_thumbnail.jpg"
        competitor_path = competitor if competitor.is_file() else None
        template_result = await thumbs.resolve_template(
            llm,
            shared_dir=ctx.settings.resolved_shared_dir,
            channel=channel,
            video_id=video_id,
            image_path=competitor_path,
            prompt_variables={
                "competitor_title": pick.get("title") or "(unknown)",
                "channel_name": channel.channel.name,
                "niche": channel.channel.niche or "(not set)",
                "language": project.language,
            },
            model=thumbs.TEMPLATE_MODEL,
            project_id=project.id,
        )
        template = template_result.template
        llm_cost = template_result.cost_usd
        if template_result.note:
            warnings.append(template_result.note)

        # 2. The SEO pack (Claude, completed from the script) and the headline options.
        _check_cancel(ctx)
        await ctx.report("Writing the title, description, tags and chapters", 25)
        if llm is None:
            warnings.append(
                "No writing model is set up (Settings > Models and providers); the metadata "
                "was built from the script."
            )
            chapters = seo_module.chapters_from(script, timing, project.format)
            seo_result = seo_module.SeoResult(
                pack=seo_module.enforce_limits(
                    seo_module.fallback_pack(title, script, chapters, channel), warnings
                ),
                title_promise_early=seo_module.title_promise_heuristic(title, script.text()),
                title_promise_note="Checked by keyword (no writing model).",
            )
        else:
            seo_result = await seo_module.build_seo_pack(
                llm,
                title=title,
                script=script,
                timing=timing,
                channel=channel,
                language=project.language,
                fmt=project.format,
                max_headline_words=max_words,
                project_id=project.id,
                notes=notes,
            )
        llm_cost += seo_result.cost_usd
        warnings.extend(seo_result.notes)
        suggested = list(seo_result.headlines)
        if edits.headline and edits.headline.strip():
            suggested.insert(0, edits.headline.strip())
        headlines = thumbs.headline_options(title, max_words, suggested)

        # 3. The text-free picture and the three variants.
        _check_cancel(ctx)
        await ctx.report("Making the thumbnail picture", 40)
        style = thumbs.style_for(channel, template, project.language)
        warnings.extend(style.notes)
        subject_prompt, negative = thumbs.subject_prompt(title, template, channel)
        subjects, subject_info, image_cost, variants = await self._make_thumbnails(
            ctx, out, subject_prompt, negative, style, template, headlines, competitor_path,
            warnings,
        )
        if image_cost:
            project.costs.images_usd = round(project.costs.images_usd + image_cost, 6)

        # 4. Where the files will go, which videos exist (nothing is copied yet).
        _check_cancel(ctx)
        await ctx.report("Checking the rendered videos", 70)
        presets = exporter.selected_presets(folder, ctx.settings, timeline)
        export_dir, folder_warnings = exporter.export_folder_for(channel, ctx.settings, project)
        warnings.extend(folder_warnings)
        _found, missing, video_names = exporter.locate_videos(folder, project.topic_slug, presets)

        # 5. Metadata, disclosure, provenance, the export record.
        chosen_id = edits.thumbnail_choice or "v1"
        disclosure = disclosure_for(
            folder, channel,
            True if edits.altered_or_synthetic is None else bool(edits.altered_or_synthetic),
        )
        metadata = self._metadata(
            project, seo_result.pack, variants, chosen_id, video_names, disclosure,
            seo_result.model, seo_result.notes,
        )
        if edits.metadata:
            metadata = apply_metadata_edits(metadata, edits.metadata, warnings)
        doc = ExportDoc(
            project_id=project.id,
            exported_at=utc_now(),
            export_folder=str(export_dir),
            topic_slug=project.topic_slug,
            presets=presets,
            missing_presets=missing,
            thumbnails=variants,
            thumbnail_choice=chosen_id,  # type: ignore[arg-type]
            template=template,
            template_source=template_result.source,
            competitor_video_id=video_id,
            competitor_thumbnail=str(competitor_path) if competitor_path else None,
            subject_provider=subject_info.get("provider", ""),
            subject_model=subject_info.get("model", ""),
            subject_files={aspect: path.name for aspect, path in subjects.items()},
            title_promise_early=seo_result.title_promise_early,
            title_promise_note=seo_result.title_promise_note,
            warnings=warnings,
            costs={"llm_usd": round(llm_cost, 6), "images_usd": round(image_cost, 6)},
            model=seo_result.model or template_result.model,
        )
        results = gates.evaluate("export", gate_context(doc))
        doc.gate_results = gates.to_dicts(results)
        await ctx.report("Writing the metadata and the provenance record", 85)
        outputs = await run_in_thread(
            ctx,
            partial(self._write_bundle, ctx, out, doc, metadata, timeline, subject_prompt,
                    headlines),
        )
        blocking = gates.blocking_reasons(results)
        if blocking:
            # The engine does not keep a payload for a blocked stage; the reviewer still
            # needs the thumbnails and the metadata to decide what to do.
            payload = review_payload(project.id, doc, metadata, max_words)
            atomic_write_text(out / REVIEW_PAYLOAD_FILE, json.dumps(payload, indent=2) + "\n")
            raise GateBlocked(blocking, cost_usd=round(llm_cost, 6))
        if project.stage_modes.for_stage(StageName.export) == "auto":
            # Nobody reviews this stage: the run is the approval.
            await ctx.report("Copying the videos to the export folder", 92)
            outputs += await self._export_files(ctx, out, doc, metadata)
        payload = review_payload(project.id, doc, metadata, max_words)
        await ctx.report("Export ready for review", 100)
        return StageResult(
            outputs=outputs,
            summary=self._summary(doc, metadata),
            cost_usd=round(llm_cost, 6),
            needs_review_payload=payload,
            gate_results=doc.gate_results,
        )

    async def _make_thumbnails(
        self,
        ctx: StageContext,
        out: Path,
        prompt: str,
        negative: str,
        style: thumbs.ThumbnailStyle,
        template: ThumbnailTemplate,
        headlines: list[str],
        competitor: Path | None,
        warnings: list[str],
    ) -> tuple[dict[str, Path], dict[str, str], float, list[Any]]:
        """Subject pictures from the image tool (a brand gradient when there is none), then
        the three variants; a new seed when the result still looks like the competitor's."""
        provider = ctx.providers.get("image")
        base_seed = _seed(ctx.project.id, ctx.project.title)
        cost = 0.0
        info: dict[str, str] = {}
        subjects: dict[str, Path] = {}
        variants: list[Any] = []
        use_provider = provider is not None
        for attempt in range(thumbs.MAX_SUBJECT_TRIES):
            _check_cancel(ctx)
            seed = (base_seed + attempt * 7919) % (1 << 31)
            for aspect in thumbs.ASPECTS:
                target = out / thumbs.SUBJECT_FILES[aspect]
                if use_provider:
                    try:
                        result = await run_in_thread(
                            ctx,
                            partial(thumbs.generate_subject, provider, prompt, negative, aspect,
                                    target, seed),
                        )
                        cost += float(result.cost_usd or 0.0)
                        info = {"provider": result.provider, "model": result.model}
                        subjects[aspect] = Path(result.path)
                        continue
                    except (ProviderError, OSError, ValueError) as exc:
                        use_provider = False
                        warnings.append(
                            f"The image tool could not make the thumbnail picture ({exc}); "
                            "a brand-colour background was used instead."
                        )
                    except Exception as exc:  # noqa: BLE001 - a broken adapter
                        log.exception("Image provider failed for the thumbnail")
                        use_provider = False
                        warnings.append(
                            f"The image tool failed on the thumbnail picture ({exc}); a "
                            "brand-colour background was used instead."
                        )
                subjects[aspect] = await run_in_thread(
                    ctx, partial(thumbs.fallback_subject, target, aspect, style, seed)
                )
                info = info or {"provider": "none", "model": "gradient"}
            await ctx.report("Composing three thumbnail variants", 55 + attempt * 5)
            variants = await run_in_thread(
                ctx,
                partial(thumbs.render_variants, out, headlines, subjects, template, style,
                        competitor),
            )
            distance = variants[0].distance if variants else None
            if distance is None or distance > thumbs.MIN_DISTANCE or not use_provider:
                break
            if attempt + 1 < thumbs.MAX_SUBJECT_TRIES:
                warnings.append(
                    f"Thumbnail try {attempt + 1} looked too much like the competitor's "
                    f"({distance} of 64 bits differ); a new picture was made."
                )
            else:
                warnings.append(
                    f"The thumbnail still looks like the competitor's after "
                    f"{thumbs.MAX_SUBJECT_TRIES} pictures ({distance} of 64 bits differ)."
                )
        return subjects, info, cost, variants

    @staticmethod
    def _metadata(
        project: Project,
        pack: SeoPack,
        variants: list[Any],
        chosen_id: str,
        video_names: dict[str, str],
        disclosure: Any,
        model: str,
        notes: list[str],
    ) -> ExportMetadata:
        chosen = next((v for v in variants if v.id == chosen_id), variants[0] if variants else None)
        return ExportMetadata(
            project_id=project.id,
            channel_slug=project.channel_slug,
            format=project.format,
            language=project.language,
            title=pack.title,
            description=pack.description,
            tags=pack.tags,
            chapters=pack.chapters,
            pinned_comment=pack.pinned_comment,
            hashtags=pack.hashtags,
            thumbnail=chosen.file if chosen else "",
            thumbnail_shorts=chosen.file_shorts if chosen else "",
            videos=video_names,
            disclosure=disclosure,
            generated_at=utc_now(),
            model=model,
            notes=list(notes),
        )

    def _write_bundle(
        self,
        ctx: StageContext,
        out: Path,
        doc: ExportDoc,
        metadata: ExportMetadata,
        timeline: dict[str, Any] | None,
        subject_prompt: str,
        headlines: list[str],
    ) -> list[Path]:
        """metadata.json, provenance.json/.md and export.json inside ``08_export``. Runs in
        a worker thread; nothing is copied to the export folder here."""
        project, channel = ctx.project, ctx.channel
        chosen = doc.chosen()
        metadata.thumbnail = chosen.file if chosen else ""
        metadata.thumbnail_shorts = chosen.file_shorts if chosen else ""
        metadata.videos = {
            preset: exporter.video_name(project.topic_slug, preset)
            for preset in doc.presets if preset not in doc.missing_presets
        }
        provenance = build_provenance(
            project=project,
            channel=channel,
            folder=ctx.folder,
            disclosure=metadata.disclosure,
            thumbnail={
                "template_source": doc.template_source,
                "competitor_video_id": doc.competitor_video_id,
                "subject_provider": doc.subject_provider,
                "subject_model": doc.subject_model,
                "subject_prompt": subject_prompt,
                "headlines": ", ".join(headlines),
                "chosen": doc.thumbnail_choice,
                "distance_to_competitor": chosen.distance if chosen else None,
            },
            render={
                "presets": ", ".join(doc.presets),
                "missing_presets": ", ".join(doc.missing_presets),
                "export_folder": doc.export_folder,
                "render_log": (ctx.folder / "07_edit" / "render.log").is_file(),
                **_timeline_summary(timeline),
            },
            seo={"model": metadata.model},
            costs={
                "llm_usd": project.costs.llm_usd + doc.costs.get("llm_usd", 0.0),
                "voice_usd": project.costs.voice_usd,
                "images_usd": project.costs.images_usd,
            },
            timeline=timeline,
        )
        _check_cancel(ctx)
        metadata_path = write_json_model(out / exporter.METADATA_FILE, metadata)
        provenance_path = write_json_model(out / exporter.PROVENANCE_JSON, provenance)
        markdown_path = out / exporter.PROVENANCE_MD
        atomic_write_text(markdown_path, provenance_markdown(provenance))
        export_path = write_json_model(out / exporter.EXPORT_FILE, doc)
        outputs = [out / v.file for v in doc.thumbnails]
        outputs += [out / v.file_shorts for v in doc.thumbnails]
        outputs += [metadata_path, provenance_path, markdown_path, export_path]
        return outputs

    async def _export_files(
        self, ctx: StageContext, out: Path, doc: ExportDoc, metadata: ExportMetadata
    ) -> list[Path]:
        """Copy the videos, the chosen thumbnails and the documents into the export folder
        (a worker thread; copies that are already there are skipped) and record it in
        ``export.json``. Returns the files written in the export folder."""
        project, channel = ctx.project, ctx.channel
        export_dir = Path(doc.export_folder)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            export_dir, folder_warnings = exporter.export_folder_for(channel, ctx.settings, project)
            doc.export_folder = str(export_dir)
            doc.warnings.extend(folder_warnings)

        def work() -> list[ExportedFile]:
            _check_cancel(ctx)
            files, missing, _names = exporter.copy_videos(
                ctx.folder, export_dir, project.topic_slug, doc.presets
            )
            doc.missing_presets = missing
            files += exporter.copy_thumbnails(
                out, export_dir, project.topic_slug, doc.chosen(), ctx.folder
            )
            files += exporter.copy_documents(out, export_dir, ctx.folder)
            return files

        doc.files = await run_in_thread(ctx, work)
        doc.exported = True
        doc.exported_at = utc_now()
        write_json_model(out / exporter.EXPORT_FILE, doc)
        return [export_dir / f.name for f in doc.files]

    async def on_approve(self, ctx: StageContext) -> StageResult | None:
        """Approve = export: copy the pack into the export folder. Called by the engine on
        every approval (after ``apply_edits``); ``None`` when the stage never ran."""
        out = ctx.stage_dir(StageName.export)
        doc = read_export_doc(out)
        metadata = read_metadata(out)
        if doc is None or metadata is None:
            return None
        await ctx.report("Copying the videos to the export folder", 10)
        outputs = await self._export_files(ctx, out, doc, metadata)
        await ctx.report("Exported", 100)
        return StageResult(
            outputs=outputs,
            summary=self._summary(doc, metadata),
            needs_review_payload=review_payload(
                ctx.project.id, doc, metadata, ctx.channel.thumbnail.max_headline_words
            ),
            gate_results=doc.gate_results,
        )

    @staticmethod
    def _summary(doc: ExportDoc, metadata: ExportMetadata) -> str:
        if doc.exported:
            videos = [f.name for f in doc.files if f.kind == "video"]
        else:
            videos = [
                exporter.video_name(doc.topic_slug, p) for p in doc.presets
                if p not in doc.missing_presets
            ]
        parts = [
            f"{len(videos)} video file(s)" + (f" ({', '.join(videos)})" if videos else ""),
            f"{len(doc.thumbnails)} thumbnail variants (chosen {doc.thumbnail_choice})",
            f"{len(metadata.chapters)} chapters",
        ]
        if doc.missing_presets:
            parts.append(f"missing: {', '.join(doc.missing_presets)}")
        where = "Exported to" if doc.exported else "Ready to export to"
        return f"{where} {doc.export_folder}: " + ", ".join(parts) + "."

    # Approval edits --------------------------------------------------------------------------

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Change the metadata, the chosen thumbnail, its headline or the disclosure flag
        and rewrite the pack in ``08_export``. No new picture, no new model call; the copy
        into the export folder is ``on_approve``'s job, which the engine calls right after."""
        if not ctx.edits or not any(k in ctx.edits for k in EDIT_KEYS):
            return None
        try:
            edits = ExportApproveEdits.model_validate(
                {k: v for k, v in ctx.edits.items() if k in EDIT_KEYS}
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise StageError(f"The export edits are not valid ({where}): {first['msg']}") from exc
        project, channel = ctx.project, ctx.channel
        out = ctx.stage_dir(StageName.export)
        doc = read_export_doc(out)
        metadata = read_metadata(out)
        if doc is None or metadata is None:
            raise StageError("The export step has not run yet, so there is nothing to change.")
        notes: list[str] = []
        if edits.thumbnail_choice:
            if doc.variant(edits.thumbnail_choice) is None:
                raise StageError(
                    f"There is no thumbnail {edits.thumbnail_choice}; choose one of "
                    + ", ".join(v.id for v in doc.thumbnails) + "."
                )
            doc.thumbnail_choice = edits.thumbnail_choice
            notes.append(f"Thumbnail {edits.thumbnail_choice} chosen by the reviewer.")
        if edits.headline is not None and edits.headline.strip():
            await self._rerender_headline(ctx, out, doc, edits.headline.strip(), notes)
        if edits.altered_or_synthetic is not None:
            metadata.disclosure.altered_or_synthetic = bool(edits.altered_or_synthetic)
            notes.append(
                "Disclosure set to "
                + ("altered or synthetic." if edits.altered_or_synthetic else "not synthetic.")
            )
        if edits.metadata:
            metadata = apply_metadata_edits(metadata, edits.metadata, notes)
        doc.warnings = [w for w in doc.warnings if not w.startswith("Reviewer:")]
        doc.warnings.extend(f"Reviewer: {n}" for n in notes)
        timeline = read_dict(ctx.folder / "07_edit" / "timeline.json") or None
        _found, missing, _names = exporter.locate_videos(
            ctx.folder, project.topic_slug, doc.presets
        )
        doc.missing_presets = missing
        doc.exported = False  # the edited pack has not been copied yet
        doc.files = []
        doc.exported_at = utc_now()
        results = gates.evaluate("export", gate_context(doc))
        doc.gate_results = gates.to_dicts(results)
        chosen = doc.chosen()
        outputs = await run_in_thread(
            ctx,
            partial(
                self._write_bundle, ctx, out, doc, metadata, timeline,
                subject_prompt="", headlines=[v.headline for v in doc.thumbnails],
            ),
        )
        return StageResult(
            outputs=outputs,
            summary="Export updated by the reviewer: " + self._summary(doc, metadata)
            + (f" Headline: {chosen.headline}." if chosen else ""),
            needs_review_payload=review_payload(
                project.id, doc, metadata, channel.thumbnail.max_headline_words
            ),
            gate_results=doc.gate_results,
        )

    async def _rerender_headline(
        self, ctx: StageContext, out: Path, doc: ExportDoc, headline: str, notes: list[str]
    ) -> None:
        """Re-draw the chosen variant's text over the subject picture already on disk."""
        chosen = doc.chosen()
        if chosen is None:
            raise StageError("There are no thumbnails to change the headline on.")
        max_words = ctx.channel.thumbnail.max_headline_words
        if len(headline.split()) > max_words:
            notes.append(
                f"The headline has more than {max_words} words; the thumbnail text may be small."
            )
        style = thumbs.style_for(ctx.channel, doc.template, ctx.project.language)
        subjects: dict[str, Path] = {}
        for aspect in thumbs.ASPECTS:
            name = doc.subject_files.get(aspect) or thumbs.SUBJECT_FILES[aspect]
            path = out / name
            if not path.is_file():
                path = await run_in_thread(
                    ctx,
                    partial(thumbs.fallback_subject, out / thumbs.SUBJECT_FILES[aspect], aspect,
                            style),
                )
            subjects[aspect] = path
        competitor = Path(doc.competitor_thumbnail) if doc.competitor_thumbnail else None
        variant = await run_in_thread(
            ctx,
            partial(thumbs.render_variant, out, chosen.id, headline, subjects, doc.template,
                    style),
        )
        variant.distance = thumbs.similarity_distance(out / variant.file, competitor)
        doc.thumbnails = [variant if v.id == chosen.id else v for v in doc.thumbnails]
        notes.append(f"Headline of thumbnail {chosen.id} changed to '{headline}'.")


__all__ = [
    "VARIANT_IDS",
    "ExportStage",
    "ExportedFile",
    "apply_metadata_edits",
    "gate_context",
    "load_review_payload",
    "read_export_doc",
    "read_metadata",
    "review_payload",
    "ScriptDoc",
]
