"""Storyboard stage: the deterministic rule fix-ups on hand-made documents, the stage on the
mock (every blocking rule holds after the fix-ups), and approval edits with locked fields."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cashflow_studio.config import Settings
from cashflow_studio.llm import MockLLMClient
from cashflow_studio.llm.config import transition_catalog
from cashflow_studio.models.channel import Channel
from cashflow_studio.models.project import Project, ProjectSource
from cashflow_studio.models.script import ScriptDoc
from cashflow_studio.models.storyboard import (
    MOTION_PRESETS,
    Scene,
    SceneLocks,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from cashflow_studio.pipeline.stages.base import StageContext, StageError
from cashflow_studio.pipeline.stages.script import make_paragraph
from cashflow_studio.pipeline.stages.storyboard import (
    DEFAULT_NEGATIVE,
    Rules,
    SentenceInfo,
    StoryboardStage,
    estimate_seconds,
    fix_motion,
    fix_popups,
    fix_prompts,
    fix_scene_lengths,
    fix_timings,
    fix_transitions,
    fix_up,
    group_for_band,
    load_review_payload,
    split_balanced,
    strip_prompt_wrapping,
    variety_of,
)

STYLE = "Cinematic photo-realism, warm light"
POPUP_STYLE = "Rounded box, brand yellow"
SENTENCES = [
    "The diner opened at five every morning without fail.",
    "Nobody noticed the stranger in the corner booth that day.",
    "He ordered coffee and watched the door for an hour.",
    "The waitress had worked a double shift and her feet ached.",
    "When the bill came he left a note folded under the plate.",
    "She read it twice before she understood what it said.",
    "The note named a debt from thirty years before.",
    "Her father had once paid for a meal he could not afford.",
    "The stranger had carried that kindness across a lifetime.",
    "She sat down in the booth and cried for a long minute.",
    "The cook turned the sign to closed and let her be.",
    "Outside the first light touched the empty parking lot.",
]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def rules(fmt: str = "long") -> Rules:
    band = (3.0, 4.0) if fmt == "shorts" else (8.0, 12.0)
    return Rules(min_s=band[0], max_s=band[1], fmt=fmt, catalog=transition_catalog(),
                 style_guide=STYLE, negative_rules="no watermarks, no distorted hands",
                 popup_style=POPUP_STYLE)


def infos(texts: list[str] | None = None, seconds: float = 4.0) -> list[SentenceInfo]:
    return [SentenceInfo(f"s-{i + 1:02d}", t, seconds) for i, t in enumerate(texts or SENTENCES)]


def scene(index: int, ids: list[str], sentences: list[SentenceInfo], **overrides: Any) -> Scene:
    by_id = {s.id: s for s in sentences}
    fields: dict[str, Any] = {
        "index": index, "sentence_ids": ids,
        "narration": " ".join(by_id[i].text for i in ids if i in by_id),
        "image_prompt": f"Scene {index} picture", "negative_prompt": "",
        "popup": ScenePopup(text=None), "motion": SceneMotion(preset="zoom_in"),
        "transition_out": SceneTransition(type="fade", duration_s=0.6),
    }
    fields.update(overrides)
    return Scene(**fields)


def transition(type_name: str, duration: float = 0.6) -> SceneTransition:
    return SceneTransition(type=type_name, duration_s=duration)


# Pure fix-ups ---------------------------------------------------------------------------------


def test_timing_and_grouping() -> None:
    assert estimate_seconds("one two three four five six seven eight nine ten", 150) == 4.0
    assert estimate_seconds("", 150) == 0.6
    groups = group_for_band(infos(), 8.0, 12.0)
    assert [len(g) for g in groups] == [2, 2, 2, 2, 2, 2]
    groups = group_for_band(infos(seconds=3.5), 3.0, 4.0)
    assert all(len(g) == 1 for g in groups)
    # Balanced splitting: no short tail.
    assert [len(c) for c in split_balanced(infos()[:4], 12.0)] == [2, 2]
    assert sorted(len(c) for c in split_balanced(infos()[:5], 12.0)) == [2, 3]
    assert sorted(len(c) for c in split_balanced(infos()[:7], 12.0)) == [2, 2, 3]
    assert [len(c) for c in split_balanced(infos()[:2], 12.0)] == [2]
    assert split_balanced([], 12.0) == []


def test_fix_scene_lengths_splits_and_merges() -> None:
    sentences = infos()  # 4 s each; band 8-12
    ids = [s.id for s in sentences]
    scenes = [
        scene(0, ids[:4], sentences),               # 16 s -> split in two
        scene(1, ids[4:5], sentences),              # 4 s  -> merged with the next
        scene(2, ids[5:6], sentences),              # 4 s
        scene(3, ids[6:9], sentences),              # 12 s ok
        scene(4, ids[9:10], sentences, locked=SceneLocks(narration=True)),  # 4 s but locked
        scene(5, ids[10:12], sentences),            # 8 s ok
    ]
    fixed = fix_scene_lengths(scenes, sentences, rules())
    fix_timings(fixed, sentences)
    assert [s.sentence_ids for s in fixed] == [
        ids[0:2], ids[2:4], ids[4:6], ids[6:9], ids[9:10], ids[10:12]
    ]
    assert [s.est_duration_s for s in fixed] == [8.0, 8.0, 8.0, 12.0, 4.0, 8.0]
    assert fixed[1].image_prompt != fixed[0].image_prompt  # the new half gets its own picture
    assert any("Split from scene 1" in n for n in fixed[1].notes)
    assert any("Merged" in n for n in fixed[2].notes)
    assert fixed[4].locked.narration and any("shorter than 8" in n for n in fixed[4].notes)
    assert fixed[0].est_start_s == 0.0 and fixed[-1].est_end_s == 48.0
    assert [s.index for s in fixed] == list(range(6))
    # A single sentence longer than the band stays alone with a note.
    long_one = [SentenceInfo("x", "a very long sentence", 15.0)]
    only = fix_scene_lengths([scene(0, ["x"], long_one)], long_one, rules())
    assert len(only) == 1 and "longer than 12" in only[0].notes[0]


def test_fix_scene_lengths_moves_a_boundary_when_merging_is_impossible() -> None:
    sentences = infos()
    ids = [s.id for s in sentences]
    scenes = [scene(0, ids[0:3], sentences), scene(1, ids[3:4], sentences)]  # 12 s + 4 s
    fixed = fix_scene_lengths(scenes, sentences, rules())
    assert [s.sentence_ids for s in fixed] == [ids[0:2], ids[2:4]]
    assert any("boundary moved" in n for n in fixed[0].notes)


def test_fix_motion_alternates_and_respects_locks() -> None:
    sentences = infos()
    scenes = [scene(i, [sentences[i].id], sentences) for i in range(4)]  # all zoom_in
    warnings: list[str] = []
    fix_motion(scenes, warnings)
    presets = [s.motion.preset for s in scenes]
    assert all(presets[i] != presets[i - 1] for i in range(1, 4))
    assert scenes[0].motion.preset == "zoom_in"
    moved = scenes[1].motion
    assert moved.start_rect != moved.end_rect or moved.preset == "hold"
    assert warnings == []

    locked = [
        scene(i, [sentences[i].id], sentences, locked=SceneLocks(motion=True)) for i in range(2)
    ]
    fix_motion(locked, warnings)
    assert [s.motion.preset for s in locked] == ["zoom_in", "zoom_in"] and len(warnings) == 1

    mixed = [scene(0, [sentences[0].id], sentences, locked=SceneLocks(motion=True)),
             scene(1, [sentences[1].id], sentences)]
    fix_motion(mixed, warnings)
    assert mixed[0].motion.preset == "zoom_in" and mixed[1].motion.preset != "zoom_in"
    assert mixed[1].motion.preset in MOTION_PRESETS


def test_fix_transitions_known_unique_and_last_scene_fades() -> None:
    sentences = infos()
    catalog = transition_catalog()
    scenes = [
        scene(0, [sentences[0].id], sentences, transition_out=transition("fade")),
        scene(1, [sentences[1].id], sentences, transition_out=transition("fade")),
        scene(2, [sentences[2].id], sentences, transition_out=transition("whoosh", 0)),
        scene(3, [sentences[3].id], sentences, transition_out=transition("wipeleft", 0.5)),
    ]
    warnings: list[str] = []
    fix_transitions(scenes, rules(), warnings)
    types = [s.transition_out.type for s in scenes]
    assert all(t in catalog.types for t in types)
    assert all(types[i] != types[i - 1] for i in range(1, 4))
    assert types[-1] == catalog.last_scene
    assert any("not in the list" in n for n in scenes[2].notes)
    assert scenes[2].transition_out.duration_s > 0
    assert warnings == []
    # Shorts get shorter transitions.
    short = [scene(0, [sentences[0].id], sentences, transition_out=transition("nope", 0)),
             scene(1, [sentences[1].id], sentences, transition_out=transition("fade", 0))]
    fix_transitions(short, rules("shorts"), warnings)
    expected = catalog.duration_for(short[0].transition_out.type, "shorts")
    assert short[0].transition_out.duration_s == expected


def test_fix_popups_length_identity_and_share() -> None:
    sentences = infos()
    scenes = [scene(i, [sentences[i].id], sentences) for i in range(6)]
    fix_timings(scenes, sentences)
    scenes[0].popup.text = "one two three four five six seven eight"
    scenes[1].popup.text = sentences[1].text          # identical to the narration
    scenes[2].popup.text = "   Keep   this  "
    scenes[3].popup.text = "Locked popup text that is far too long for the rule"
    scenes[3].locked = SceneLocks(popup=True)
    warnings: list[str] = []
    fix_popups(scenes, sentences, rules(), warnings)
    assert scenes[0].popup.text == "one two three four five six"
    assert scenes[1].popup.text and scenes[1].popup.text.lower() != sentences[1].text.lower()
    assert len(scenes[1].popup.text.split()) <= 3
    assert scenes[2].popup.text == "Keep this" and scenes[2].popup.style == POPUP_STYLE
    assert scenes[3].popup.text.startswith("Locked popup text that is far")  # untouched
    first = scenes[0]
    assert 0 <= first.popup.in_offset_s < first.popup.out_offset_s <= first.est_duration_s
    assert warnings == []

    bare = [scene(i, [sentences[i].id], sentences) for i in range(10)]
    fix_timings(bare, sentences)
    fix_popups(bare, sentences, rules(), warnings)
    with_text = [s for s in bare if s.has_text]
    assert len(with_text) == 4  # ceil(0.35 * 10)
    assert all(len(s.popup.text.split()) <= 6 for s in with_text)
    assert all("added by the app" in s.notes[-1] for s in with_text)
    positions = {s.popup.position for s in with_text}
    assert len(positions) > 1


def test_fix_prompts_prefix_suffix_idempotent() -> None:
    sentences = infos()
    scenes = [scene(0, [sentences[0].id], sentences, image_prompt="A diner at dawn, wide shot.",
                    negative_prompt="text, letters"),
              scene(1, [sentences[1].id], sentences, image_prompt="",
                    locked=SceneLocks(image_prompt=True))]
    fix_prompts(scenes, rules())
    prompt = scenes[0].image_prompt
    assert prompt.startswith(f"{STYLE}. A diner at dawn, wide shot. Avoid: text, letters, ")
    assert "no watermarks" in prompt and prompt.endswith(".")
    negative = scenes[0].negative_prompt
    assert "deformed hands" in negative and negative.count("text") == 1
    assert scenes[1].image_prompt == ""  # locked prompt untouched
    assert scenes[1].negative_prompt.startswith("no watermarks")  # negatives still filled
    before = scenes[0].image_prompt
    fix_prompts(scenes, rules())
    assert scenes[0].image_prompt == before  # applying the rule twice changes nothing
    assert strip_prompt_wrapping(before, STYLE) == "A diner at dawn, wide shot"
    assert DEFAULT_NEGATIVE.split(",")[0] == "text"


def test_fix_up_covers_every_sentence_once_in_order() -> None:
    sentences = infos()
    ids = [s.id for s in sentences]
    doc = StoryboardDoc(
        project_id="p", format="long", aspect="16:9", generated_at=datetime.now(UTC),
        model="mock",
        scenes=[
            scene(0, [ids[1], ids[0]], sentences),        # out of order
            scene(1, [ids[2], ids[2], "ghost"], sentences, popup=ScenePopup(text="Hi")),
            # sentences 4..12 missing from the draft
        ],
    )
    fix_up(doc, sentences, rules())
    covered = [i for s in doc.scenes for i in s.sentence_ids]
    assert covered == ids
    durations = [s.est_duration_s for s in doc.scenes]
    assert all(8.0 <= d <= 12.0 for d in durations), durations
    assert doc.variety.scenes == len(doc.scenes) and doc.variety.popup_share >= 0.35
    assert doc.variety.distinct_transitions >= 2
    presets = [s.motion.preset for s in doc.scenes]
    assert all(presets[i] != presets[i - 1] for i in range(1, len(presets)))
    assert all(s.image_prompt.startswith(STYLE) for s in doc.scenes)
    assert doc.scenes[-1].transition_out.type == transition_catalog().last_scene
    assert variety_of(doc.scenes, rules(), []).warnings == []


# The stage ----------------------------------------------------------------------------------------


def write_script(folder: Path, title: str, fmt: str = "long",
                 texts: list[str] | None = None) -> ScriptDoc:
    texts = texts or SENTENCES
    paragraphs = [make_paragraph(0, i, " ".join(texts[i * 3:(i + 1) * 3]), False)
                  for i in range((len(texts) + 2) // 3)]
    doc = ScriptDoc.model_validate({
        "title": title, "language": "English", "format": fmt, "target_words": 100,
        "word_count": sum(len(t.split()) for t in texts), "model": "mock",
        "generated_at": datetime.now(UTC), "speaking_rate_wpm": 150,
        "sections": [{"id": "sec-01", "name": "Story", "purpose": "",
                      "paragraphs": [p.model_dump() for p in paragraphs]}],
    })
    script_dir = folder / "03_script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "script.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    (script_dir / "speech.json").write_text(json.dumps({
        "language": "English", "model": "mock", "generated_at": datetime.now(UTC).isoformat(),
        "sentences": [{"id": s.id, "text": s.text, "speech_text": s.text}
                      for s in doc.sentences()],
    }), encoding="utf-8")
    return doc


def make_ctx(app_env, *, fmt: str = "long", project_id: str = "p1", style_guide: str = STYLE,
             notes: list[str] | None = None) -> StageContext:
    settings = Settings()
    folder = app_env.projects_dir / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    project = Project(id=project_id, channel_slug="kind-ledger", topic_slug="diner",
                      title="The Diner Debt", format=fmt, created_at=now, updated_at=now,
                      folder=str(folder),
                      source=ProjectSource(kind="own_topic", topic_text="The Diner Debt"))
    channel = Channel.model_validate({
        "slug": "kind-ledger", "channel": {"name": "Kind Ledger"},
        "images": {"style_guide": style_guide,
                   "negative_rules": "no watermarks, no distorted hands",
                   "popup_style": POPUP_STYLE, "aspect_long": "16:9", "aspect_shorts": "9:16"},
    })
    return StageContext(project=project, channel=channel, settings=settings, folder=folder,
                        providers={"llm": MockLLMClient(app_data_dir=settings.app_data_dir)},
                        notes=notes or [])


def read_doc(ctx: StageContext) -> StoryboardDoc:
    path = ctx.folder / "04_storyboard" / "storyboard.json"
    return StoryboardDoc.model_validate_json(path.read_text(encoding="utf-8"))


def test_run_on_the_mock_satisfies_every_rule(app_env) -> None:
    ctx = make_ctx(app_env)
    script = write_script(ctx.folder, "The Diner Debt")
    result = run(StoryboardStage().run(ctx))
    path = ctx.folder / "04_storyboard" / "storyboard.json"
    assert result.outputs == [path]
    doc = read_doc(ctx)
    assert doc.project_id == "p1" and doc.aspect == "16:9" and doc.style_guide == STYLE
    sentence_ids = [s.id for s in script.sentences()]
    assert [i for s in doc.scenes for i in s.sentence_ids] == sentence_ids
    for s in doc.scenes:
        assert s.est_duration_s <= 12.0
        assert s.image_prompt.startswith(STYLE + ". ") and "Avoid:" in s.image_prompt
        assert "no watermarks" in s.negative_prompt
        assert s.transition_out.type in transition_catalog().types
        assert (s.popup.text is None) or len(s.popup.text.split()) <= 6
        assert s.popup.style == POPUP_STYLE
        assert s.image.status == "pending" and s.image.path is None
        assert s.locked.model_dump() == SceneLocks().model_dump()
    in_band = [s for s in doc.scenes if 8.0 <= s.est_duration_s <= 12.0]
    assert len(in_band) >= len(doc.scenes) - 2  # only a short tail may fall outside
    assert doc.scenes[0].est_start_s == 0.0
    assert doc.scenes[-1].est_end_s == pytest.approx(doc.total_s)
    presets = [s.motion.preset for s in doc.scenes]
    assert all(presets[i] != presets[i - 1] for i in range(1, len(presets)))
    types = [s.transition_out.type for s in doc.scenes]
    assert all(types[i] != types[i - 1] for i in range(1, len(types)))
    assert doc.variety.popup_share >= 0.35
    blocking = [g for g in result.gate_results if g["severity"] == "block" and not g["passed"]]
    assert blocking == []
    assert {g["id"] for g in result.gate_results} >= {
        "storyboard.motion_alternates", "storyboard.popup_share"
    }
    # The mock's deliberate mistakes were corrected (notes say so).
    notes = [n for s in doc.scenes for n in s.notes]
    assert any("Popup shortened" in n for n in notes)
    assert any("Transition changed" in n or "Motion changed" in n for n in notes)
    payload = result.needs_review_payload
    assert payload["stage"] == "storyboard" and payload["scene_band_s"] == [8.0, 12.0]
    assert payload["transitions"][0]["label"] and len(payload["motion_presets"]) == 8
    assert json.dumps(payload)
    assert load_review_payload(ctx.folder)["storyboard"]["scenes"]
    assert "scenes" in result.summary
    call = ctx.providers["llm"].calls[0]
    assert call["task"] == "storyboard" and call["variables"]["min_s"] == 8.0


def test_shorts_use_the_short_band_and_vertical_aspect(app_env) -> None:
    ctx = make_ctx(app_env, fmt="shorts", project_id="s1")
    write_script(ctx.folder, "The Diner Debt", fmt="shorts", texts=SENTENCES[:6])
    result = run(StoryboardStage().run(ctx))
    doc = read_doc(ctx)
    assert doc.aspect == "9:16" and doc.format == "shorts"
    # One sentence per scene: 9-12 word sentences are 3.6-4.8 s and cannot be split further.
    assert all(len(s.sentence_ids) == 1 for s in doc.scenes)
    assert all(3.0 <= s.est_duration_s <= 5.0 for s in doc.scenes)
    assert doc.scenes[0].transition_out.duration_s < transition_catalog().duration_for("fade")
    assert result.needs_review_payload["scene_band_s"] == [3.0, 4.0]


def test_apply_edits_revalidates_and_respects_locks(app_env) -> None:
    ctx = make_ctx(app_env, project_id="e1")
    write_script(ctx.folder, "The Diner Debt")
    run(StoryboardStage().run(ctx))
    data = json.loads(
        (ctx.folder / "04_storyboard" / "storyboard.json").read_text(encoding="utf-8")
    )
    first, second = data["scenes"][0], data["scenes"][1]
    first["locked"]["motion"] = True
    first["motion"]["preset"] = "pan_down"
    second["motion"]["preset"] = "pan_down"               # clashes with the locked neighbour
    second["popup"]["text"] = "one two three four five six seven eight"
    second["transition_out"]["type"] = "not-a-transition"
    first["image_prompt"] = "My own hand-written prompt"
    first["locked"]["image_prompt"] = True
    ctx.edits = {"storyboard": data}
    result = run(StoryboardStage().apply_edits(ctx))
    doc = read_doc(ctx)
    assert doc.scenes[0].motion.preset == "pan_down"
    assert doc.scenes[1].motion.preset != "pan_down"
    assert doc.scenes[1].popup.text == "one two three four five six"
    assert doc.scenes[1].transition_out.type in transition_catalog().types
    assert doc.scenes[0].image_prompt == "My own hand-written prompt"
    assert doc.scenes[0].locked.image_prompt and doc.scenes[0].locked.motion
    assert all(g["passed"] for g in result.gate_results if g["severity"] == "block")
    assert "edited by the reviewer" in result.summary

    ctx.edits = {"storyboard": {**data, "scenes": []}}
    with pytest.raises(StageError, match="at least one scene"):
        run(StoryboardStage().apply_edits(ctx))
    ctx.edits = {"storyboard": {"nonsense": True}}
    with pytest.raises(StageError, match="not valid"):
        run(StoryboardStage().apply_edits(ctx))
    ctx.edits = {}
    assert run(StoryboardStage().apply_edits(ctx)) is None


def test_missing_script_is_a_plain_error(app_env) -> None:
    ctx = make_ctx(app_env, project_id="none")
    with pytest.raises(StageError, match="script step has not produced"):
        run(StoryboardStage().run(ctx))


def test_redo_keeps_locked_fields_and_sentence_groups(app_env) -> None:
    """A regenerate (the engine calls ``run`` again) must not throw away locked work."""
    ctx = make_ctx(app_env, project_id="redo")
    write_script(ctx.folder, "The Diner Debt")
    run(StoryboardStage().run(ctx))
    data = json.loads(
        (ctx.folder / "04_storyboard" / "storyboard.json").read_text(encoding="utf-8")
    )
    first, second, last = data["scenes"][0], data["scenes"][1], data["scenes"][-1]
    first["image_prompt"] = "My own hand-written prompt"
    first["locked"]["image_prompt"] = True
    first["motion"]["preset"] = "pan_down"
    first["locked"]["motion"] = True
    second["popup"]["text"] = "Keep this popup"
    second["locked"]["popup"] = True
    second["transition_out"]["type"] = "wipeleft"
    second["locked"]["transition_out"] = True
    last["locked"]["narration"] = True
    ctx.edits = {"storyboard": data}
    run(StoryboardStage().apply_edits(ctx))
    approved = read_doc(ctx)
    assert approved.scenes[0].image_prompt == "My own hand-written prompt"

    ctx.edits = {}
    ctx.notes = ["Scene 3: show the street at night"]
    run(StoryboardStage().run(ctx))
    redone = read_doc(ctx)
    scene = redone.scenes[0]
    assert scene.image_prompt == "My own hand-written prompt" and scene.locked.image_prompt
    assert scene.motion.preset == "pan_down" and scene.locked.motion
    assert any("Kept from the previous storyboard" in n for n in scene.notes)
    assert redone.scenes[1].popup.text == "Keep this popup" and redone.scenes[1].locked.popup
    assert redone.scenes[1].transition_out.type == "wipeleft"
    assert redone.scenes[1].locked.transition_out
    assert redone.scenes[-1].sentence_ids == approved.scenes[-1].sentence_ids
    assert redone.scenes[-1].locked.narration
    # Every sentence is still covered exactly once and the unlocked scenes were re-planned.
    covered = [i for s in redone.scenes for i in s.sentence_ids]
    assert covered == [i for s in approved.scenes for i in s.sentence_ids]
    assert not redone.scenes[2].locked.any
    # The model was told what is locked.
    call = ctx.providers["llm"].calls[-1]
    locked_lines = call["variables"]["locked_scenes"]
    assert any("pan_down" in line for line in locked_lines)
    assert any("Keep this popup" in line for line in locked_lines)
    assert call["variables"]["notes"] == ["Scene 3: show the street at night"]
