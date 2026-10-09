"""Export stage on a synthetic 3-scene project with the mocks: the bundle in 08_export, the
export folder layout and copies, metadata and provenance contents, the review payload with
file URLs, the gates (files present, thumbnail similarity), approval edits, redo edits, the
image-tool fallback and the cancel flag. Nothing touches the network."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from cashcow_studio.config import Settings
from cashcow_studio.export import exporter
from cashcow_studio.export import thumbnail as thumbs
from cashcow_studio.llm import MockLLMClient
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.export import ExportDoc, ThumbnailVariant
from cashcow_studio.models.project import Project, ProjectCreate, ProjectSource, StageName
from cashcow_studio.models.script import ScriptDoc
from cashcow_studio.pipeline.engine import InvalidTransition, PipelineEngine
from cashcow_studio.pipeline.stages.base import (
    GateBlocked,
    StageContext,
    StageError,
    StageResult,
)
from cashcow_studio.pipeline.stages.export import (
    ExportStage,
    gate_context,
    load_review_payload,
    read_export_doc,
    read_metadata,
)
from cashcow_studio.pipeline.stages.script import make_paragraph
from cashcow_studio.policy import gates
from cashcow_studio.providers.image.mock import MockImageProvider
from cashcow_studio.providers.registry import UnavailableImageProvider
from cashcow_studio.storage.channel_store import ChannelStore
from conftest import channel_payload

SLUG = "kind-ledger"
TITLE = "The Waiter Who Never Forgot a Face"
TOPIC = "waiter-face"
VIDEO_ID = "abcdefghijk"
SECTIONS = [
    ("Hook", ["The waiter who never forgot a face started with one coffee.",
              "Nobody in the diner knew what that cup would cost him."]),
    ("Setup", ["Every morning the same man sat in the corner booth.",
               "He paid in coins and never said a word."]),
    ("Turn", ["One winter the man stopped coming and a letter arrived instead.",
              "Inside was a key and a single line of thanks."]),
    ("Ending", ["The waiter kept the key on the wall above the till.",
                "He says a face is a promise you keep."]),
]
THUMBNAIL_FILES = (
    "thumbnail.png", "thumbnail_v2.png", "thumbnail_v3.png",
    "thumbnail_shorts.png", "thumbnail_shorts_v2.png", "thumbnail_shorts_v3.png",
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def small_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    """1K subject pictures keep the tests quick (the stage asks for 2K)."""
    monkeypatch.setattr(thumbs, "SUBJECT_SIZE", "1K")


def make_channel(**overrides: Any) -> Channel:
    data: dict[str, Any] = {
        "slug": SLUG,
        "channel": {"name": "Kind Ledger", "niche": "Kindness stories", "audience": "Adults",
                    "brand_colors": ["#1F3864", "#FFC000"]},
        "voice": {"tool": "minimax", "model": "speech-2.8-hd", "clone_ref": "voice-123"},
        "images": {"tool": "google_gemini", "model": "gemini-nano-banana-2.1",
                   "style_guide": "Cinematic photo-realism, warm light"},
        "thumbnail": {"headline_colors": "#FFFFFF on #1F3864, accent #FFC000",
                      "max_headline_words": 4},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return Channel.model_validate(data)


def build_project(folder: Path, *, presets: list[str] | None = None,
                  rendered: tuple[str, ...] = ("720p",), fmt: str = "long",
                  competitor: bool = True, timing: bool = True) -> Project:
    now = write_project_files(folder, presets=presets, rendered=rendered, fmt=fmt,
                              competitor=competitor, timing=timing)
    return Project(id="p-export", channel_slug=SLUG, topic_slug=TOPIC, title=TITLE,
                   format=fmt, created_at=now, updated_at=now, folder=str(folder),
                   source=ProjectSource(kind="ai_pick", video_id=VIDEO_ID))


def write_project_files(folder: Path, *, presets: list[str] | None = None,
                        rendered: tuple[str, ...] = ("720p",), fmt: str = "long",
                        competitor: bool = True, timing: bool = True) -> datetime:
    """The files the earlier stages leave behind for a 3-scene, 8-sentence video."""
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for si, (name, sentences) in enumerate(SECTIONS):
        paragraph = make_paragraph(si, 0, " ".join(sentences), False)
        rows.append({"id": f"sec-{si + 1:02d}", "name": name, "purpose": "",
                     "paragraphs": [paragraph.model_dump()]})
    doc = ScriptDoc.model_validate({
        "title": TITLE, "language": "English", "format": fmt, "target_words": 80,
        "word_count": 80, "model": "mock", "generated_at": now, "sections": rows,
    })
    (folder / "03_script").mkdir(exist_ok=True)
    (folder / "03_script" / "script.json").write_text(doc.model_dump_json(indent=2), "utf-8")
    (folder / "02_title").mkdir(exist_ok=True)
    (folder / "02_title" / "title.json").write_text(json.dumps({
        "variants": [{"index": 0, "title": TITLE, "viral_score": 8, "flags": []},
                     {"index": 1, "title": "Other", "viral_score": 5, "flags": ["too long"]}],
        "chosen_index": 0,
    }), "utf-8")
    clock = 0.0
    timing_rows = []
    for sentence in doc.sentences():
        timing_rows.append({"id": sentence.id, "text": sentence.text, "start_s": clock,
                            "end_s": clock + 12.0, "words": []})
        clock += 12.0
    (folder / "05_voice").mkdir(exist_ok=True)
    if timing:
        (folder / "05_voice" / "timing.json").write_text(json.dumps({
            "sample_rate": 48000, "duration_s": clock, "source": "estimated",
            "sentences": timing_rows,
        }), "utf-8")
    (folder / "05_voice" / "consent.json").write_text(json.dumps({"owner_name": "Imran"}), "utf-8")
    (folder / "01_research").mkdir(exist_ok=True)
    (folder / "01_research" / "pick.json").write_text(json.dumps({
        "kind": "ai_pick", "video_id": VIDEO_ID, "title": "A Waiter's Secret",
        "channel_name": "Human Ember", "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
        "strategy": "top_outlier_fresh", "reason": "12x the usual views",
        "picked_at": now.isoformat(), "candidate": {"views": 1200000, "outlier_score": 12.3},
    }), "utf-8")
    if competitor:
        image = Image.new("RGB", (640, 360), (30, 30, 90))
        draw = ImageDraw.Draw(image)
        draw.rectangle((380, 40, 600, 330), fill=(220, 180, 60))
        draw.rectangle((30, 220, 340, 330), fill=(255, 255, 255))
        image.save(folder / "01_research" / "competitor_thumbnail.jpg", "JPEG")
    (folder / "04_storyboard").mkdir(exist_ok=True)
    (folder / "04_storyboard" / "storyboard.json").write_text(json.dumps({
        "model": "mock", "style_guide": "Cinematic photo-realism, warm light",
        "scenes": [{"index": i, "image_prompt": f"Scene {i} picture.", "negative_prompt": "text"}
                   for i in range(3)],
    }), "utf-8")
    (folder / "06_images").mkdir(exist_ok=True)
    (folder / "06_images" / "images.json").write_text(json.dumps({
        "scenes": [{"scene": i, "path": f"06_images/scene_{i:02d}.png", "provider": "mock",
                    "model": "mock-placeholder", "seed": i,
                    "provenance": {"c2pa": False, "synthid": False},
                    "qa": {"matches_prompt": True, "score": 8, "reason": "ok"}}
                   for i in range(3)],
    }), "utf-8")
    (folder / "07_edit").mkdir(exist_ok=True)
    for preset in rendered:
        (folder / "07_edit" / f"final_{preset}.mp4").write_bytes(
            b"\x00\x00\x00\x1cftyp" + b"\0" * 2000
        )
    (folder / "07_edit" / "render.log").write_text("rendered\n", "utf-8")
    (folder / "07_edit" / "timeline.json").write_text(json.dumps({
        "version": 1, "presets": presets if presets is not None else list(rendered),
        "duration_s": clock, "width": 1280, "height": 720, "fps": 30, "scenes": [{}, {}, {}],
        "music": {"path": None, "license_ok": False}, "captions": {"enabled": True},
    }), "utf-8")
    return now


def make_ctx(app_env, project: Project, *, channel: Channel | None = None,
             providers: dict[str, Any] | None = None, edits: dict[str, Any] | None = None,
             notes: list[str] | None = None) -> StageContext:
    settings = Settings()
    if providers is None:
        providers = {"llm": MockLLMClient(app_data_dir=settings.app_data_dir),
                     "image": MockImageProvider()}
    return StageContext(
        project=project, channel=channel or make_channel(), settings=settings,
        folder=Path(project.folder), providers=providers, edits=edits or {},
        notes=notes or [],
    )


def export_dir(app_env, project: Project) -> Path:
    return app_env.exports_dir / SLUG / f"2026-10-09_{TOPIC}"


# The run --------------------------------------------------------------------------------------


def test_run_writes_the_bundle_and_exports_the_files(app_env) -> None:
    folder = app_env.projects_dir / f"2026-10-09_{SLUG}_{TOPIC}"
    project = build_project(folder)
    ctx = make_ctx(app_env, project)
    result = run(ExportStage().run(ctx))

    out = folder / "08_export"
    for name in THUMBNAIL_FILES + ("subject_16x9.png", "subject_9x16.png", "metadata.json",
                                   "provenance.json", "provenance.md", "export.json"):
        assert (out / name).is_file(), name
    with Image.open(out / "thumbnail.png") as opened:
        assert opened.size == (1280, 720)
    with Image.open(out / "thumbnail_shorts_v3.png") as opened:
        assert opened.size == (1080, 1920)
    assert {p.name for p in result.outputs} >= {"thumbnail.png", "metadata.json", "export.json"}

    # Approve = export: the run leaves the pack in 08_export and the export folder empty.
    target = export_dir(app_env, project)
    assert target.is_dir() and list(target.iterdir()) == []
    assert result.needs_review_payload["exported"] is False
    assert result.needs_review_payload["files"] == []
    assert result.summary.startswith(f"Ready to export to {target}")
    assert read_export_doc(out).exported is False
    approved = run(ExportStage().on_approve(ctx))
    assert approved is not None and approved.summary.startswith(f"Exported to {target}")
    assert read_export_doc(out).exported is True
    assert {p.name for p in approved.outputs} >= {f"{TOPIC}_720p.mp4", "metadata.json"}

    # The export folder: <exports>/<channel>/<date>_<topic>/ with the contract's names.
    names = sorted(p.name for p in target.iterdir())
    assert names == sorted([
        f"{TOPIC}_720p.mp4", f"{TOPIC}_thumbnail.png", f"{TOPIC}_thumbnail_shorts.png",
        "metadata.json", "provenance.json", "provenance.md",
    ])
    assert (target / f"{TOPIC}_720p.mp4").read_bytes() == \
        (folder / "07_edit" / "final_720p.mp4").read_bytes()
    assert (target / f"{TOPIC}_thumbnail.png").read_bytes() == (out / "thumbnail.png").read_bytes()

    metadata = json.loads((out / "metadata.json").read_text("utf-8"))
    assert metadata["title"] == TITLE and metadata["language"] == "English"
    assert [c["time"] for c in metadata["chapters"]] == ["00:00", "00:24", "00:48", "01:12"]
    assert metadata["tags"] and len(", ".join(metadata["tags"])) <= 500
    assert metadata["thumbnail"] == "thumbnail.png"
    assert metadata["thumbnail_shorts"] == "thumbnail_shorts.png"
    assert metadata["videos"] == {"720p": f"{TOPIC}_720p.mp4"}
    disclosure = metadata["disclosure"]
    assert disclosure["altered_or_synthetic"] is True
    assert [i["scene"] for i in disclosure["provenance"]["images"]] == [0, 1, 2]
    assert disclosure["provenance"]["images"][0]["model"] == "mock-placeholder"
    assert disclosure["provenance"]["voice"]["provider"] == "minimax"
    assert disclosure["provenance"]["voice"]["consent_ref"].endswith("consent.json")
    assert json.loads((target / "metadata.json").read_text("utf-8")) == metadata

    provenance = json.loads((out / "provenance.json").read_text("utf-8"))
    assert provenance["research"]["pick"]["video_id"] == VIDEO_ID
    assert provenance["research"]["pick"]["views"] == 1200000
    assert provenance["title_variants"][0]["chosen"] is True
    assert provenance["script"]["word_count"] == 80
    assert [s["scene"] for s in provenance["prompts"]["scenes"]] == [0, 1, 2]
    assert provenance["qa"][0]["score"] == 8
    assert provenance["render"]["presets"] == "720p" and provenance["render"]["render_log"]
    assert provenance["thumbnail"]["competitor_video_id"] == VIDEO_ID
    assert provenance["licences"][-1]["licence"].startswith("SIL Open Font")
    assert "review_log" in provenance and "export" in provenance["review_log"]
    text = (out / "provenance.md").read_text("utf-8")
    for heading in ("# Provenance:", "## Disclosure", "## Research", "## Title options",
                    "## Script", "## Pictures", "## Prompts", "## Thumbnail", "## Licences",
                    "## Review log"):
        assert heading in text, heading
    assert "A Waiter's Secret" in text and "(chosen)" in text

    # Gates, costs, summary and the review payload with file URLs.
    assert [g["id"] for g in result.gate_results] == [
        "export.thumbnail_similarity", "export.title_promise", "export.files_present",
    ]
    assert all(g["passed"] for g in result.gate_results), result.gate_results
    assert result.cost_usd == 0.0 and ctx.project.costs.images_usd == 0.0
    assert str(target) in result.summary and "3 thumbnail variants" in result.summary
    payload = approved.needs_review_payload
    assert payload["exported"] is True
    assert payload["stage"] == "export" and payload["thumbnail_choice"] == "v1"
    assert [t["id"] for t in payload["thumbnails"]] == ["v1", "v2", "v3"]
    assert payload["thumbnails"][0]["url"] == "/api/projects/p-export/files/08_export/thumbnail.png"
    assert payload["thumbnails"][1]["url_shorts"] == \
        "/api/projects/p-export/files/08_export/thumbnail_shorts_v2.png"
    assert len({t["headline"] for t in payload["thumbnails"]}) == 3
    assert all(t["distance"] > thumbs.MIN_DISTANCE for t in payload["thumbnails"])
    assert payload["metadata"]["title"] == TITLE and payload["export_folder"] == str(target)
    assert payload["presets"] == ["720p"] and payload["missing_presets"] == []
    assert payload["provenance_url"] == "/api/projects/p-export/files/08_export/provenance.json"
    assert payload["max_headline_words"] == 4 and payload["title_promise_early"] is True
    assert {f["kind"] for f in payload["files"]} == {"video", "thumbnail", "metadata", "provenance"}
    assert json.dumps(payload)
    # The template was read once and cached in the channel folder.
    cache = app_env.shared_dir / "channels" / SLUG / "thumbnail-templates" / f"{VIDEO_ID}.json"
    assert cache.is_file()
    doc = read_export_doc(out)
    assert doc is not None and doc.template_source in ("llm", "neutral")
    assert doc.subject_provider == "mock" and doc.competitor_thumbnail
    # A restart rebuilds the payload from the files.
    assert load_review_payload(folder, "p-export", 4)["thumbnails"][2]["id"] == "v3"


def test_second_run_reads_the_cached_template(app_env) -> None:
    folder = app_env.projects_dir / "second"
    project = build_project(folder)
    run(ExportStage().run(make_ctx(app_env, project)))
    llm = MockLLMClient(app_data_dir=app_env.app_data_dir)
    ctx = make_ctx(app_env, project, providers={"llm": llm, "image": MockImageProvider()})
    run(ExportStage().run(ctx))
    assert read_export_doc(folder / "08_export").template_source == "cache"
    assert all(c["task"] != "thumbnail_template" for c in llm.calls)


def test_files_present_gate_blocks_but_leaves_everything_in_place(app_env) -> None:
    folder = app_env.projects_dir / "blocked"
    project = build_project(folder, presets=["720p", "1080p"], rendered=("720p",))
    ctx = make_ctx(app_env, project)
    with pytest.raises(GateBlocked) as excinfo:
        run(ExportStage().run(ctx))
    assert "1080p" in str(excinfo.value) and "720p" not in excinfo.value.reasons[0].split(":")[0]
    out = folder / "08_export"
    for name in THUMBNAIL_FILES + ("metadata.json", "provenance.md", "export.json"):
        assert (out / name).is_file(), name
    target = export_dir(app_env, project)
    assert not (target / f"{TOPIC}_720p.mp4").exists()  # nothing is copied before approval
    doc = read_export_doc(out)
    assert doc.missing_presets == ["1080p"] and doc.presets == ["720p", "1080p"]
    assert doc.exported is False and read_metadata(out).videos == {"720p": f"{TOPIC}_720p.mp4"}
    failed = [g for g in doc.gate_results if not g["passed"]]
    assert [g["id"] for g in failed] == ["export.files_present"]
    # The reviewer still gets the payload (the engine keeps none for a blocked stage).
    payload = json.loads((out / "review_payload.json").read_text("utf-8"))
    assert payload["missing_presets"] == ["1080p"] and len(payload["thumbnails"]) == 3


def test_similarity_gate_blocks_a_copy_after_retries(app_env, monkeypatch) -> None:
    folder = app_env.projects_dir / "copy"
    project = build_project(folder)
    monkeypatch.setattr(thumbs, "similarity_distance", lambda thumbnail, competitor: 4)
    ctx = make_ctx(app_env, project)
    with pytest.raises(GateBlocked) as excinfo:
        run(ExportStage().run(ctx))
    assert "too much like the competitor" in str(excinfo.value)
    doc = read_export_doc(folder / "08_export")
    assert sum("a new picture was made" in w for w in doc.warnings) == thumbs.MAX_SUBJECT_TRIES - 1
    assert sum("still looks like" in w for w in doc.warnings) == 1
    assert doc.thumbnails[0].distance == 4
    context = gate_context(doc)
    assert context["thumbnail_distance"] == 4 and context["competitor_thumbnail"] is True
    results = gates.evaluate("export", context)
    assert [r.passed for r in results] == [False, True, True]


def test_no_competitor_thumbnail_and_no_timing_still_export(app_env) -> None:
    folder = app_env.projects_dir / "plain"
    project = build_project(folder, competitor=False, timing=False)
    result = run(ExportStage().run(make_ctx(app_env, project)))
    assert all(g["passed"] for g in result.gate_results)
    doc = read_export_doc(folder / "08_export")
    assert doc.template_source == "neutral" and doc.competitor_thumbnail is None
    assert all(v.distance is None for v in doc.thumbnails)
    assert any("timing" in w for w in doc.warnings)
    metadata = read_metadata(folder / "08_export")
    # Estimated from 80 words at 150 wpm the video is 32 s long: too short for chapters.
    assert metadata.chapters == [] and metadata.description


def test_channel_export_folder_is_used_when_it_can_be(app_env, tmp_path: Path) -> None:
    folder = app_env.projects_dir / "channel-folder"
    project = build_project(folder)
    custom = tmp_path / "drive" / "exports" / SLUG
    channel = make_channel(channel={"export_folder": str(custom)})
    ctx = make_ctx(app_env, project, channel=channel)
    result = run(ExportStage().run(ctx))
    run(ExportStage().on_approve(ctx))
    target = custom / f"2026-10-09_{TOPIC}"  # the slug is not repeated
    assert target.is_dir() and (target / f"{TOPIC}_720p.mp4").is_file()
    assert result.needs_review_payload["export_folder"] == str(target)
    assert not (app_env.exports_dir / SLUG).exists()

    # A relative (unusable) folder falls back to the app's exports folder with a warning.
    project2 = build_project(app_env.projects_dir / "relative")
    channel = make_channel(channel={"export_folder": "exports/here"})
    result = run(ExportStage().run(make_ctx(app_env, project2, channel=channel)))
    assert export_dir(app_env, project2).is_dir()
    assert any("not a full path" in w for w in result.needs_review_payload["warnings"])


def test_image_tool_problems_fall_back_to_a_brand_background(app_env) -> None:
    folder = app_env.projects_dir / "no-images"
    project = build_project(folder)
    llm = MockLLMClient(app_data_dir=app_env.app_data_dir)
    unavailable = UnavailableImageProvider("gemini", "No GEMINI_API_KEY is set.")
    result = run(ExportStage().run(make_ctx(app_env, project,
                                            providers={"llm": llm, "image": unavailable})))
    payload = result.needs_review_payload
    assert any("brand-colour background" in w for w in payload["warnings"])
    assert all(g["passed"] for g in result.gate_results)
    doc = read_export_doc(folder / "08_export")
    assert doc.subject_provider == "none" and doc.subject_model == "gradient"
    with Image.open(folder / "08_export" / "subject_9x16.png") as opened:
        assert opened.size == (1080, 1920)

    # No image tool at all, and no writing model: still a complete export.
    project2 = build_project(app_env.projects_dir / "nothing")
    result = run(ExportStage().run(make_ctx(app_env, project2, providers={})))
    assert all(g["passed"] for g in result.gate_results)
    assert any("No writing model" in w for w in result.needs_review_payload["warnings"])
    assert result.needs_review_payload["metadata"]["chapters"]


def test_cancel_flag_stops_the_run_before_files_are_written(app_env) -> None:
    folder = app_env.projects_dir / "cancelled"
    project = build_project(folder)
    ctx = make_ctx(app_env, project)
    ctx.cancel.set()
    with pytest.raises(StageError, match="archived"):
        run(ExportStage().run(ctx))
    assert not (folder / "08_export" / "export.json").exists()
    assert not export_dir(app_env, project).exists()


def test_missing_script_is_a_plain_error(app_env) -> None:
    folder = app_env.projects_dir / "no-script"
    project = build_project(folder)
    (folder / "03_script" / "script.json").unlink()
    with pytest.raises(StageError, match="script step"):
        run(ExportStage().run(make_ctx(app_env, project)))


# Edits -----------------------------------------------------------------------------------------


def test_apply_edits_changes_choice_headline_metadata_and_disclosure(app_env) -> None:
    folder = app_env.projects_dir / "edits"
    project = build_project(folder)
    ctx = make_ctx(app_env, project)
    run(ExportStage().run(ctx))
    out = folder / "08_export"
    before = (out / "thumbnail_v2.png").read_bytes()

    ctx.edits = {
        "thumbnail_choice": "v2",
        "headline": "He Kept Every Promise",
        "metadata": {"title": "He Never Forgot a Face", "tags": ["waiter", "kindness", "waiter"],
                     "chapters": [{"time": "00:03", "title": "Start"},
                                  {"time": "00:30", "title": "Middle"}, "01:00 End"],
                     "mystery_field": 1},
        "altered_or_synthetic": False,
        "override_gates": True,
    }
    result = run(ExportStage().apply_edits(ctx))
    assert result is not None
    payload = result.needs_review_payload
    assert payload["thumbnail_choice"] == "v2"
    assert payload["thumbnails"][1]["headline"] == "He Kept Every Promise"
    assert payload["thumbnails"][0]["headline"] != "He Kept Every Promise"
    assert (out / "thumbnail_v2.png").read_bytes() != before
    metadata = payload["metadata"]
    assert metadata["title"] == "He Never Forgot a Face"
    assert metadata["tags"] == ["waiter", "kindness"]
    assert [(c["time"], c["title"]) for c in metadata["chapters"]] == [
        ("00:00", "Start"), ("00:30", "Middle"), ("01:00", "End"),
    ]
    assert metadata["disclosure"]["altered_or_synthetic"] is False
    assert metadata["thumbnail"] == "thumbnail_v2.png"
    assert any("mystery_field" in w for w in payload["warnings"])
    assert any("Thumbnail v2 chosen" in w for w in payload["warnings"])
    assert all(g["passed"] for g in result.gate_results)
    assert "He Kept Every Promise" in result.summary
    # Edits rewrite the pack; the approval that carries them copies it to the folder.
    assert payload["exported"] is False
    target = export_dir(app_env, project)
    assert not (target / f"{TOPIC}_thumbnail.png").exists()
    approved = run(ExportStage().on_approve(ctx))
    assert approved is not None and approved.needs_review_payload["exported"] is True
    # The export folder now holds the chosen thumbnail and the edited metadata.
    assert (target / f"{TOPIC}_thumbnail.png").read_bytes() == \
        (out / "thumbnail_v2.png").read_bytes()
    assert (target / f"{TOPIC}_thumbnail_shorts.png").read_bytes() == \
        (out / "thumbnail_shorts_v2.png").read_bytes()
    exported = json.loads((target / "metadata.json").read_text("utf-8"))
    assert exported["title"] == "He Never Forgot a Face"
    assert exported["disclosure"]["altered_or_synthetic"] is False
    assert json.loads((out / "export.json").read_text("utf-8"))["thumbnail_choice"] == "v2"
    provenance = json.loads((out / "provenance.json").read_text("utf-8"))
    assert provenance["thumbnail"]["chosen"] == "v2"
    assert provenance["disclosure"]["altered_or_synthetic"] is False


def test_auto_mode_exports_on_the_run_itself(app_env) -> None:
    """Nobody reviews an auto export stage, so the run is the approval and copies at once."""
    folder = app_env.projects_dir / "auto"
    project = build_project(folder)
    project.stage_modes.export = "auto"
    result = run(ExportStage().run(make_ctx(app_env, project)))
    target = export_dir(app_env, project)
    assert (target / f"{TOPIC}_720p.mp4").is_file() and (target / "metadata.json").is_file()
    assert result.needs_review_payload["exported"] is True
    assert result.summary.startswith(f"Exported to {target}")
    assert read_export_doc(folder / "08_export").exported is True


def test_unchanged_copies_are_skipped(tmp_path: Path) -> None:
    source = tmp_path / "final_720p.mp4"
    source.write_bytes(b"x" * 10)
    target = tmp_path / "out" / "video_720p.mp4"
    copied = exporter.copy_file(source, target, tmp_path, "video")
    assert copied.bytes == 10 and exporter.same_file_already_there(source, target)
    source.write_bytes(b"y" * 11)
    assert not exporter.same_file_already_there(source, target)
    assert exporter.copy_file(source, target, tmp_path, "video").bytes == 11
    assert target.read_bytes() == b"y" * 11
    assert not exporter.same_file_already_there(source, tmp_path / "missing.mp4")


def test_apply_edits_rejects_bad_input_and_ignores_unrelated_keys(app_env) -> None:
    folder = app_env.projects_dir / "bad-edits"
    project = build_project(folder)
    ctx = make_ctx(app_env, project)
    ctx.edits = {"override_gates": True}
    assert run(ExportStage().apply_edits(ctx)) is None
    ctx.edits = {"thumbnail_choice": "v2"}
    with pytest.raises(StageError, match="has not run yet"):
        run(ExportStage().apply_edits(ctx))
    run(ExportStage().run(make_ctx(app_env, project)))
    ctx.edits = {"thumbnail_choice": "v9"}
    with pytest.raises(StageError, match="not valid"):
        run(ExportStage().apply_edits(ctx))
    ctx.edits = {"metadata": {"title": "   "}}
    with pytest.raises(StageError, match="cannot be empty"):
        run(ExportStage().apply_edits(ctx))
    ctx.edits = {"metadata": {"tags": 42}}
    with pytest.raises(StageError, match="list of words"):
        run(ExportStage().apply_edits(ctx))


def test_redo_edits_shape_the_new_run(app_env) -> None:
    folder = app_env.projects_dir / "redo"
    project = build_project(folder)
    ctx = make_ctx(app_env, project, edits={
        "headline": "the key on the wall above the till", "thumbnail_choice": "v3",
        "altered_or_synthetic": False, "metadata": {"pinned_comment": "Tell us your story."},
    }, notes=["Make the headline about the key"])
    result = run(ExportStage().run(ctx))
    payload = result.needs_review_payload
    assert payload["thumbnails"][0]["headline"] == "the key on the"  # capped to 4 words
    assert payload["thumbnail_choice"] == "v3"
    assert payload["metadata"]["thumbnail"] == "thumbnail_v3.png"
    assert payload["metadata"]["pinned_comment"] == "Tell us your story."
    assert payload["disclosure"]["altered_or_synthetic"] is False
    llm = ctx.providers["llm"]
    seo_call = next(c for c in llm.calls if c["task"] == "seo")
    assert seo_call["variables"]["notes"] == ["Make the headline about the key"]


def test_gate_context_and_export_doc_helpers() -> None:
    doc = ExportDoc(
        project_id="p", exported_at=datetime.now(UTC), export_folder="x", topic_slug="t",
        presets=["1080p"], missing_presets=["1080p"],
        thumbnails=[ThumbnailVariant(id="v1", headline="a", file="thumbnail.png",
                                     file_shorts="thumbnail_shorts.png", distance=40),
                    ThumbnailVariant(id="v2", headline="b", file="thumbnail_v2.png",
                                     file_shorts="thumbnail_shorts_v2.png", distance=9)],
        thumbnail_choice="v2", competitor_thumbnail="c.jpg", title_promise_early=False,
        title_promise_note="Only at the end.",
    )
    assert doc.chosen().id == "v2" and doc.variant("v3") is None
    results = {r.id: r for r in gates.evaluate("export", gate_context(doc))}
    assert results["export.thumbnail_similarity"].passed is False
    assert results["export.title_promise"].passed is False
    assert results["export.title_promise"].detail == "Only at the end."
    assert results["export.files_present"].passed is False
    assert "1080p" in results["export.files_present"].detail
    assert gates.blocking_reasons(list(results.values()))[0].startswith("Thumbnail is not a copy")


# Through the engine ---------------------------------------------------------------------------


class PassThroughStage:
    """Stands in for an earlier stage; the edit stand-in writes the files export reads."""

    def __init__(self, name: str, *, presets: list[str] | None = None) -> None:
        self.name = StageName(name)
        self.presets = presets

    async def run(self, ctx: StageContext) -> StageResult:
        if self.name == StageName.research:
            ctx.project.title = TITLE
        if self.name == StageName.edit:
            write_project_files(ctx.folder, presets=self.presets)
        return StageResult(summary=f"{self.name.value} done")


def make_engine(app_env, *, presets: list[str] | None = None) -> tuple[PipelineEngine, Channel]:
    settings = Settings()
    settings.ensure_dirs()
    body = channel_payload("Kind Ledger") | {"slug": SLUG}
    body["thumbnail"] = {"headline_colors": "#FFFFFF on #1F3864, accent #FFC000",
                         "max_headline_words": 4}
    channel = ChannelStore(settings.resolved_shared_dir).create(Channel.model_validate(body))
    engine = PipelineEngine(settings, providers={
        "llm": MockLLMClient(app_data_dir=settings.app_data_dir), "image": MockImageProvider(),
    })
    for name in ("research", "title", "script", "storyboard", "voice", "images", "edit"):
        engine.register(PassThroughStage(name, presets=presets))
    engine.register(ExportStage())
    return engine, channel


def create_project(engine: PipelineEngine, channel: Channel, **modes: str) -> Project:
    overrides = {name.value: "auto" for name in StageName} | modes
    body = ProjectCreate(
        channel_slug=channel.slug, format="long",
        source=ProjectSource(kind="ai_pick"),
        stage_mode_overrides={StageName(k): v for k, v in overrides.items()},  # type: ignore[misc]
    )
    return engine.create_project(body, channel)


async def settle(engine: PipelineEngine, project_id: str, timeout: float = 90.0) -> None:
    task = engine.tasks.get(project_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    await asyncio.sleep(0)


def test_engine_runs_the_export_stage_and_applies_approval_edits(app_env) -> None:
    async def main() -> None:
        engine, channel = make_engine(app_env)
        project = create_project(engine, channel, export="review")
        await engine.run(project.id)
        await settle(engine, project.id)
        parked = engine.get(project.id)
        assert parked.stages[StageName.export].status == "awaiting_review", (
            parked.stages[StageName.export].error
        )
        assert parked.current_stage == StageName.export
        payload = engine.stage_payload(project.id, StageName.export)
        assert payload["stage"] == "export" and len(payload["thumbnails"]) == 3
        assert payload["thumbnails"][0]["url"].startswith(f"/api/projects/{project.id}/files/")
        folder = Path(parked.folder)
        assert (folder / "08_export" / "metadata.json").is_file()
        target = Path(payload["export_folder"])
        assert target.parent == app_env.exports_dir / SLUG
        # Approve = export: nothing is in the export folder while the review is pending.
        assert payload["exported"] is False
        assert not (target / f"{parked.topic_slug}_720p.mp4").exists()
        assert "Finished in" in " ".join(parked.stages[StageName.export].history)

        approved = await engine.approve(
            project.id, StageName.export, "Imran", "ship it",
            edits={"thumbnail_choice": "v2", "metadata": {"title": "He Never Forgot a Face"}},
        )
        assert approved.stages[StageName.export].status == "approved"
        await settle(engine, project.id)
        done = engine.get(project.id)
        assert done.stages[StageName.export].status == "done"
        assert done.stages[StageName.export].approved_by == "Imran"
        history = " ".join(done.stages[StageName.export].history)
        assert "Edits applied by Imran: metadata, thumbnail_choice" in history
        assert "On approval by Imran: Exported to" in history
        payload = engine.stage_payload(project.id, StageName.export)
        assert payload["exported"] is True
        assert (target / f"{done.topic_slug}_720p.mp4").is_file()
        assert payload["thumbnail_choice"] == "v2"
        assert payload["metadata"]["title"] == "He Never Forgot a Face"
        exported = json.loads((target / "metadata.json").read_text("utf-8"))
        assert exported["title"] == "He Never Forgot a Face"
        assert (target / f"{done.topic_slug}_thumbnail.png").read_bytes() == \
            (folder / "08_export" / "thumbnail_v2.png").read_bytes()
        assert done.costs.llm_usd == 0.0 and done.costs.images_usd == 0.0

    run(main())


def test_engine_reports_a_blocked_export_and_lets_the_reviewer_override(app_env) -> None:
    async def main() -> None:
        engine, channel = make_engine(app_env, presets=["720p", "1080p"])
        project = create_project(engine, channel, export="review")
        await engine.run(project.id)
        await settle(engine, project.id)
        blocked = engine.get(project.id)
        state = blocked.stages[StageName.export]
        assert state.status == "failed" and "1080p" in (state.error or "")
        assert state.gate_results and state.gate_results[0]["severity"] == "block"
        # The thumbnails and metadata are still there for the reviewer to look at.
        payload = engine.stage_payload(project.id, StageName.export)
        assert payload["missing_presets"] == ["1080p"] and len(payload["thumbnails"]) == 3

        with pytest.raises(InvalidTransition, match="still blocks"):
            await engine.approve(project.id, StageName.export, "Imran",
                                 edits={"thumbnail_choice": "v3"})
        approved = await engine.approve(
            project.id, StageName.export, "Imran",
            edits={"thumbnail_choice": "v3", "override_gates": True},
        )
        assert approved.stages[StageName.export].status == "approved"
        await settle(engine, project.id)
        done = engine.get(project.id)
        assert done.stages[StageName.export].status == "done"
        assert "overridden by Imran" in " ".join(done.stages[StageName.export].history)
        payload = engine.stage_payload(project.id, StageName.export)
        assert payload["thumbnail_choice"] == "v3" and payload["exported"] is True
        assert (Path(payload["export_folder"]) / f"{done.topic_slug}_720p.mp4").is_file()

    run(main())
