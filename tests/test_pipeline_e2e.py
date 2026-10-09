"""The whole pipeline end to end through the HTTP API with the REAL stages and every
provider mocked (docs/M1-M2-CONTRACT.md section 10, docs/M3-M4-CONTRACT.md section 7):

    create from ai_pick -> research awaiting_review -> approve -> title awaiting_review (7
    variants) -> approve with chosen_index -> script awaiting_review -> approve -> storyboard
    awaiting_review (scenes) -> approve -> voice (auto: voice.wav + timing.json, the clock)
    -> images awaiting_review (one checked picture per scene) -> approve -> edit
    awaiting_review (timeline.json, proxy.mp4, final_<size>.mp4) -> approve -> export
    awaiting_review (thumbnails, metadata.json, provenance, the export folder) -> approve
    -> done

plus the own_topic and manual_pick starts, an all-auto run and a voice recording supplied by
hand. Nothing touches the network or spends money. The channel is set to a one-minute video
so the renders stay small (3 to 8 scenes, a few seconds of encoding); the final sizes come
from ``CCS_E2E_PRESETS`` (default ``720p``; set ``1080p`` when there is time for it).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cashcow_studio.app import create_app
from conftest import AppEnv, channel_payload

SLUG = "kind-ledger"
MOCK_PICK = "mkAv0000005"  # the obvious one-of-ten outlier in the Human Ember fixture
RESEARCH_FILES = (
    "candidates.json", "pick.json", "video.json", "transcript.json", "competitor_thumbnail.jpg",
)
SCRIPT_FILES = ("script.md", "script.json", "speech.json", "originality.json")
VOICE_FILES = ("voice.wav", "timing.json", "voice.json")
EXPORT_FILES = (
    "thumbnail.png", "thumbnail_v2.png", "thumbnail_v3.png", "thumbnail_shorts.png",
    "metadata.json", "provenance.json", "provenance.md", "export.json",
)
TIMING_SOURCES = ("provider_word", "provider_sentence", "estimated", "aligned")
STAGE_ORDER = ("research", "title", "script", "storyboard", "voice", "images", "edit", "export")
E2E_PRESETS = [
    p.strip() for p in os.environ.get("CCS_E2E_PRESETS", "720p").split(",") if p.strip()
]
"""The final sizes the edit step renders in the full run (proxy is always made)."""
RENDER_TIMEOUT_S = 300.0
FFMPEG_READY = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
NEEDS_FFMPEG = "ffmpeg and ffprobe are needed from the edit step on"
SAMPLE_RATE = 48000


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    monkeypatch.setenv("CCS_IMAGE_SIZE", "1K")  # small placeholder pictures
    # Settings > Render: the sizes under test, encoded as fast as x264 can.
    app_env.app_data_dir.mkdir(parents=True, exist_ok=True)
    app_env.settings_file.write_text(
        json.dumps({"render": {"default_presets": E2E_PRESETS, "x264_preset": "ultrafast"}}),
        encoding="utf-8",
    )
    music_dir = write_music_folder(app_env.shared_dir / "music")
    with TestClient(create_app()) as client:
        stages = {stage.value for stage in client.app.state.engine.stages}
        assert set(STAGE_ORDER) <= stages, stages
        payload = channel_payload("Kind Ledger")
        payload["channel"]["long_form_minutes"] = 1  # keeps the renders tiny
        payload["channel"]["music_folder"] = str(music_dir)
        response = client.post("/api/channels", json=payload)
        assert response.status_code == 201, response.text
        yield client


def write_tone(path: Path, seconds: float, hz: float = 220.0, volume: int = 6000) -> Path:
    """A 48 kHz mono 16-bit sine tone written with the standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = int(SAMPLE_RATE * seconds)
    frames = b"".join(
        struct.pack("<h", int(volume * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)))
        for i in range(count)
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(frames)
    return path


def write_music_folder(folder: Path) -> Path:
    """One short tone plus the LICENSE file the music gate looks for."""
    write_tone(folder / "calm-tone.wav", 3.0)
    (folder / "LICENSE.txt").write_text(
        "Test tone made by the test suite. Free to use in any video.\n", encoding="utf-8"
    )
    return folder


def probe(path: Path) -> dict[str, Any]:
    """Duration, size and the stream kinds of a media file (ffprobe)."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    return {
        "duration": float(data["format"]["duration"]),
        "video": video is not None,
        "audio": any(s.get("codec_type") == "audio" for s in streams),
        "size": (int(video["width"]), int(video["height"])) if video else None,
    }


def wait_for(
    client: TestClient,
    project_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if predicate(body):
            return body
        failed = {
            name: state["error"]
            for name, state in body["stages"].items()
            if state["status"] == "failed"
        }
        assert not failed, f"a stage failed: {failed}"
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; stages: {statuses(body)}")
        time.sleep(0.05)


def statuses(project: dict[str, Any]) -> dict[str, str]:
    return {name: state["status"] for name, state in project["stages"].items()}


def at_gate(stage: str, status: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["stages"][stage]["status"] == status and body["current_stage"] == stage


def create(client: TestClient, source: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"channel_slug": SLUG, "format": "long", "source": source}
    body.update(overrides)
    response = client.post("/api/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def approve(
    client: TestClient, project_id: str, stage: str, edits: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"by": "Imran", "notes": f"{stage} looks good"}
    if edits:
        body["edits"] = edits
    response = client.post(f"/api/projects/{project_id}/stage/{stage}/approve", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def job_json(project: dict[str, Any]) -> dict[str, Any]:
    return json.loads((Path(project["folder"]) / "job.json").read_text(encoding="utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_job_json_matches(client: TestClient, project_id: str) -> dict[str, Any]:
    """job.json (the truth) agrees with what the API returns."""
    api_view = client.get(f"/api/projects/{project_id}").json()
    on_disk = job_json(api_view)
    assert on_disk["id"] == api_view["id"]
    assert on_disk["title"] == api_view["title"]
    assert on_disk["current_stage"] == api_view["current_stage"]
    assert statuses(on_disk) == statuses(api_view)
    assert list(on_disk["stages"]) == list(STAGE_ORDER)
    return api_view


def test_ai_pick_runs_through_every_stage_to_the_export(
    api: TestClient, app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = create(api, {"kind": "ai_pick"})
    assert Path(created["folder"]).parent == app_env.projects_dir
    assert Path(created["folder"]).name.endswith("_kind-ledger_ai-pick")

    # Research: all competitors scanned, ranked together, one AI pick.
    project = wait_for(api, created["id"], at_gate("research", "awaiting_review"))
    # Research learned the video's title, so the folder on disk now says which video it
    # holds instead of "ai-pick" (files are the truth; Explorer must make sense).
    folder = Path(project["folder"])
    assert folder.parent == app_env.projects_dir and folder.is_dir()
    assert project["topic_slug"] != "ai-pick" and not folder.name.endswith("_ai-pick")
    assert folder.name.endswith(f"_kind-ledger_{project['topic_slug']}")
    assert not Path(created["folder"]).exists()
    for name in ("01_research", "02_title", "03_script", "04_storyboard", "05_voice", "08_export"):
        assert (folder / name).is_dir(), name
    for name in RESEARCH_FILES:
        assert (folder / "01_research" / name).is_file(), name
    payload = api.get(f"/api/projects/{created['id']}/stage/research").json()
    assert payload["stage"] == "research" and payload["source_kind"] == "ai_pick"
    assert payload["pick"]["video_id"] == payload["pick_video_id"] == MOCK_PICK
    assert len(payload["channels"]) == 2 and all(c["error"] is None for c in payload["channels"])
    assert payload["candidates"][0]["rank"] == 1
    assert payload["candidates"][0]["video_id"] == MOCK_PICK
    assert project["title"] == payload["pick"]["title"]
    candidates = read_json(folder / "01_research" / "candidates.json")
    assert len(candidates["channels"]) == 2 and len(candidates["candidates"]) >= 50
    assert project["stages"]["research"]["attempts"] == 1
    assert_job_json_matches(api, created["id"])

    # Title: seven variants, one recommended, none of the flagged ones.
    approve(api, created["id"], "research")
    project = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    assert statuses(project)["research"] == "done"
    assert (folder / "02_title" / "title.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/title").json()
    title_doc = payload["title"]
    assert len(title_doc["variants"]) == 7
    recommended = title_doc["recommended_index"]
    assert recommended is not None
    assert title_doc["variants"][recommended]["flags"] == []
    assert project["title"] == title_doc["variants"][recommended]["title"]
    chosen = 1 if recommended != 1 else 2
    chosen_title = title_doc["variants"][chosen]["title"]

    # Approve with chosen_index: the chosen title becomes the project title.
    approved = approve(api, created["id"], "title", edits={"chosen_index": chosen})
    assert approved["title"] == chosen_title
    saved_title = read_json(folder / "02_title" / "title.json")
    assert saved_title["chosen_index"] == chosen and saved_title["chosen_title"] == chosen_title

    # Script: four files, originality gates passed, about one minute of narration.
    project = wait_for(api, created["id"], at_gate("script", "awaiting_review"))
    assert statuses(project)["title"] == "done"
    for name in SCRIPT_FILES:
        assert (folder / "03_script" / name).is_file(), name
    payload = api.get(f"/api/projects/{created['id']}/stage/script").json()
    assert payload["script"]["title"] == chosen_title
    assert payload["script"]["sections"] and 100 <= payload["script"]["word_count"] <= 260
    assert payload["originality"]["passed"] is True
    assert "## " in payload["script_md"]
    assert project["stages"]["script"]["gate_results"]

    # Storyboard: scenes, variety summary, every transition from the catalogue.
    approve(api, created["id"], "script")
    project = wait_for(api, created["id"], at_gate("storyboard", "awaiting_review"))
    assert (folder / "04_storyboard" / "storyboard.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/storyboard").json()
    doc = payload["storyboard"]
    assert doc["aspect"] == "16:9" and doc["format"] == "long"
    assert len(doc["scenes"]) >= 3
    types = {t["type"] for t in payload["transitions"]}
    for scene in doc["scenes"]:
        assert scene["transition_out"]["type"] in types
        assert scene["popup"]["text"] is None or len(scene["popup"]["text"].split()) <= 6
    assert doc["variety"]["scenes"] == len(doc["scenes"])
    scene_count = len(doc["scenes"])
    scene_sentence_ids = {sid for scene in doc["scenes"] for sid in scene["sentence_ids"]}

    # Voice runs on its own (the channel's default mode is auto): voice.wav plus timing.json,
    # the clock for everything after it. The project then waits at the images review.
    approve(api, created["id"], "storyboard")
    project = wait_for(api, created["id"], at_gate("images", "awaiting_review"), timeout=120)
    assert statuses(project)["voice"] == "done"
    voice_dir = folder / "05_voice"
    for name in VOICE_FILES:
        assert (voice_dir / name).is_file(), name
    timing = read_json(voice_dir / "timing.json")
    assert timing["source"] in TIMING_SOURCES and timing["duration_s"] > 0
    assert timing["sample_rate"] == SAMPLE_RATE
    assert timing["sentences"] and timing["sentences"][0]["start_s"] == 0.0
    assert {s["id"] for s in timing["sentences"]} == scene_sentence_ids
    for sentence in timing["sentences"]:
        assert sentence["end_s"] > sentence["start_s"] and sentence["words"]
        assert (voice_dir / "sentences" / f"{sentence['id']}.wav").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/voice").json()
    assert payload["stage"] == "voice" and payload["cost_kind"] == "voice"
    assert len(payload["sentences"]) == len(timing["sentences"])
    assert payload["source"] == timing["source"]
    assert payload["timing_confidence"] == timing["timing_confidence"]
    first = payload["sentences"][0]
    assert first["play_url"].startswith(f"/api/projects/{created['id']}/files/05_voice/")
    served = api.get(first["play_url"])
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("audio/wav")
    assert served.headers.get("accept-ranges") == "bytes"
    partial = api.get(payload["voice_url"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206 and len(partial.content) == 100
    assert api.get(f"/api/projects/{created['id']}/files/../job.json").status_code == 404

    # Images: one accepted picture per scene, each checked by the (mock) vision model.
    images_dir = folder / "06_images"
    assert (images_dir / "images.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/images").json()
    assert payload["stage"] == "images" and payload["cost_kind"] == "images"
    assert payload["accepted"] == payload["total"] == scene_count
    assert len(payload["scenes"]) == scene_count
    for scene in payload["scenes"]:
        assert (images_dir / scene["file"]).is_file(), scene["file"]
        assert scene["image_url"].startswith(f"/api/projects/{created['id']}/files/06_images/")
        assert scene["status"] in ("generated", "approved")
        assert scene["verdict"].startswith("Passed")
    expected_names = [f"scene_{n:02d}.png" for n in range(1, scene_count + 1)]
    assert sorted(p.name for p in images_dir.glob("scene_*.png")) == expected_names
    picture = api.get(payload["scenes"][0]["image_url"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    storyboard = read_json(folder / "04_storyboard" / "storyboard.json")
    assert all(scene["image"]["path"] for scene in storyboard["scenes"])
    assert all(
        gate["passed"] for gate in project["stages"]["images"]["gate_results"]
        if gate["severity"] == "block"
    )
    assert_job_json_matches(api, created["id"])

    if not FFMPEG_READY:
        pytest.skip(NEEDS_FFMPEG)

    # Edit: the timeline on the voice clock, the proxy and the chosen final sizes.
    approve(api, created["id"], "images")
    project = wait_for(
        api, created["id"], at_gate("edit", "awaiting_review"), timeout=RENDER_TIMEOUT_S
    )
    edit_dir = folder / "07_edit"
    timeline = read_json(edit_dir / "timeline.json")
    assert timeline["presets"] == E2E_PRESETS
    assert len(timeline["scenes"]) == scene_count
    assert timeline["scenes"][0]["start_s"] == 0.0
    assert timeline["duration_s"] >= timing["duration_s"]
    assert timeline["voice"]["path"].endswith("voice.wav")
    assert timeline["music"]["path"] and timeline["music"]["license_ok"] is True
    assert Path(timeline["music"]["path"]).name == "calm-tone.wav"
    rendered_names = ["proxy.mp4", *(f"final_{preset}.mp4" for preset in E2E_PRESETS)]
    for name in (*rendered_names, "render.log"):
        assert (edit_dir / name).is_file(), name
    if timeline["captions"]["enabled"] and timeline["captions"]["cues"]:
        assert (edit_dir / "captions.ass").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/edit").json()
    assert payload["proxy_url"] == f"/api/projects/{created['id']}/files/07_edit/proxy.mp4"
    renders = {row["preset"]: row for row in payload["renders"]}
    assert renders["proxy"]["status"] == "done"
    for preset in E2E_PRESETS:
        assert renders[preset]["status"] == "done", renders[preset]
        assert renders[preset]["play_url"].endswith(f"/07_edit/final_{preset}.mp4")
    assert payload["music_license_ok"] is True
    assert all(
        gate["passed"] for gate in project["stages"]["edit"]["gate_results"]
        if gate["severity"] == "block"
    )
    partial = api.get(payload["proxy_url"], headers={"Range": "bytes=0-1023"})
    assert partial.status_code == 206 and len(partial.content) == 1024
    # ffprobe agrees: both streams, the timeline's length, the preset's frame size.
    for name in rendered_names:
        info = probe(edit_dir / name)
        assert info["video"] and info["audio"], name
        assert abs(info["duration"] - timeline["duration_s"]) <= 1.0, (name, info)
    assert probe(edit_dir / "proxy.mp4")["size"] == (854, 480)
    if "720p" in E2E_PRESETS:
        assert probe(edit_dir / "final_720p.mp4")["size"] == (1280, 720)
    if "1080p" in E2E_PRESETS:
        assert probe(edit_dir / "final_1080p.mp4")["size"] == (1920, 1080)

    # Export: thumbnails, metadata, provenance, and the files in the export folder.
    approve(api, created["id"], "edit")
    project = wait_for(api, created["id"], at_gate("export", "awaiting_review"), timeout=120)
    export_dir = folder / "08_export"
    for name in EXPORT_FILES:
        assert (export_dir / name).is_file(), name
    with Image.open(export_dir / "thumbnail.png") as image:
        assert image.size == (1280, 720)
    with Image.open(export_dir / "thumbnail_shorts.png") as image:
        assert image.size == (1080, 1920)
    payload = api.get(f"/api/projects/{created['id']}/stage/export").json()
    assert payload["stage"] == "export"
    assert [t["id"] for t in payload["thumbnails"]] == ["v1", "v2", "v3"]
    assert payload["thumbnail_choice"] == "v1"
    for thumbnail in payload["thumbnails"]:
        assert thumbnail["headline"]
        assert api.get(thumbnail["url"]).status_code == 200
    metadata = payload["metadata"]
    assert metadata["title"] and metadata["description"]
    assert len(",".join(metadata["tags"])) <= 500
    if metadata["chapters"]:
        assert metadata["chapters"][0]["time"] == "00:00"
    assert payload["disclosure"]["altered_or_synthetic"] is True
    assert len(payload["disclosure"]["provenance"]["images"]) == scene_count
    assert payload["disclosure"]["provenance"]["voice"]["provider"] == "mock"
    assert payload["missing_presets"] == []
    assert set(payload["presets"]) >= set(E2E_PRESETS)
    exported = Path(payload["export_folder"])
    assert exported.is_dir() and exported.parent == app_env.exports_dir / SLUG
    slug = project["topic_slug"]
    assert exported.name.endswith(f"_{slug}")
    # Approve = export: nothing leaves the project folder while the review is pending.
    assert payload["exported"] is False and payload["files"] == []
    assert list(exported.iterdir()) == []
    assert api.get(payload["provenance_url"]).status_code == 200
    # "Open folder" on the export screen opens the export folder, not the project folder.
    opened: list[Path] = []
    monkeypatch.setattr("cashcow_studio.api.projects.open_folder", opened.append)
    response = api.post(f"/api/projects/{created['id']}/open-folder", json={"target": "export"})
    assert response.status_code == 200, response.text
    assert Path(response.json()["folder"]) == exported and opened == [exported]
    response = api.post(f"/api/projects/{created['id']}/open-folder", json={})
    assert Path(response.json()["folder"]) == folder

    # Approve = the export is final; every stage is done and the list shows it finished.
    approve(api, created["id"], "export")
    project = wait_for(
        api, created["id"], lambda body: body["stages"]["export"]["status"] == "done"
    )
    assert statuses(project) == dict.fromkeys(STAGE_ORDER, "done")
    assert project["stages"]["title"]["approved_by"] == "Imran"
    # The approval copied the pack into the export folder with the contract's names.
    assert "On approval by Imran: Exported to" in " ".join(project["stages"]["export"]["history"])
    payload = api.get(f"/api/projects/{created['id']}/stage/export").json()
    assert payload["exported"] is True
    assert {f["kind"] for f in payload["files"]} == {"video", "thumbnail", "metadata", "provenance"}
    for preset in E2E_PRESETS:
        assert (exported / f"{slug}_{preset}.mp4").is_file()
    for name in (
        f"{slug}_thumbnail.png", f"{slug}_thumbnail_shorts.png", "metadata.json",
        "provenance.json", "provenance.md",
    ):
        assert (exported / name).is_file(), name
    on_disk = read_json(exported / "metadata.json")
    assert on_disk["title"] == metadata["title"]
    assert on_disk["videos"] == {preset: f"{slug}_{preset}.mp4" for preset in E2E_PRESETS}
    assert on_disk["disclosure"]["altered_or_synthetic"] is True
    assert isinstance(read_json(exported / "provenance.json"), dict)
    assert (exported / "provenance.md").read_text(encoding="utf-8").strip()
    final = assert_job_json_matches(api, created["id"])
    assert final["costs"] == {"llm_usd": 0.0, "voice_usd": 0.0, "images_usd": 0.0}  # mocks are free
    rows = api.get("/api/projects").json()
    assert [row["id"] for row in rows] == [created["id"]]
    assert rows[0]["current_stage"] == "export" and rows[0]["status"] == "done"
    assert rows[0]["title"] == chosen_title

    # The picked competitor video is now "used before" for this channel: it drops out of the
    # eligible list (excluded videos are listed after the eligible ones, so ask for them all).
    candidates = api.get(f"/api/channels/{SLUG}/research/candidates?format=long&limit=500").json()
    used = {c["video_id"]: c for c in candidates["candidates"] if c["used_before"]}
    assert MOCK_PICK in used
    assert used[MOCK_PICK]["excluded_reason"]
    assert candidates["candidates"][0]["video_id"] != MOCK_PICK


def test_own_topic_skips_research_and_starts_at_the_title(api: TestClient) -> None:
    topic = "The waiter who never forgot a face"
    created = create(api, {"kind": "own_topic", "topic_text": topic})
    assert statuses(created)["research"] == "skipped"
    assert created["title"] == "The waiter who never forgot a face"
    assert not any((Path(created["folder"]) / "01_research").iterdir())

    project = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    payload = api.get(f"/api/projects/{created['id']}/stage/title").json()
    assert len(payload["title"]["variants"]) == 7
    assert payload["title"]["source_title"] == "The waiter who never forgot a face"
    assert project["stages"]["research"]["status"] == "skipped"

    # Approving with typed text works as well as choosing a variant.
    typed = "He Remembered Every Face"
    approved = approve(api, created["id"], "title", edits={"title_text": typed})
    assert approved["title"] == typed
    project = wait_for(api, created["id"], at_gate("script", "awaiting_review"))
    assert project["title"] == "He Remembered Every Face"


def test_manual_pick_uses_the_chosen_video(api: TestClient) -> None:
    other = "mkAv0000012"
    created = create(api, {"kind": "manual_pick", "video_id": other})
    assert created["source"]["video_url"] == f"https://www.youtube.com/watch?v={other}"
    project = wait_for(api, created["id"], at_gate("research", "awaiting_review"))
    folder = Path(project["folder"])  # renamed after the video's title, see the test above
    payload = api.get(f"/api/projects/{created['id']}/stage/research").json()
    assert payload["source_kind"] == "manual_pick"
    assert payload["pick_video_id"] == other
    pick = read_json(folder / "01_research" / "pick.json")
    assert pick["kind"] == "manual_pick" and pick["video_id"] == other
    assert project["title"] == pick["title"]

    # The reviewer changes the pick before the title runs.
    approved = approve(api, created["id"], "research", edits={"video_id": MOCK_PICK})
    assert approved["folder"] == project["folder"]  # renamed once, not on every change
    pick = read_json(folder / "01_research" / "pick.json")
    assert pick["video_id"] == MOCK_PICK
    assert approved["title"] == pick["title"]
    # The person's choice is the project's source now, so a redo would keep it.
    assert approved["source"]["kind"] == "manual_pick"
    assert approved["source"]["video_id"] == MOCK_PICK
    assert approved["source"]["video_url"] == f"https://www.youtube.com/watch?v={MOCK_PICK}"
    wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    # Only the current pick counts as "used" for the channel; the overridden one is free.
    candidates = api.get(f"/api/channels/{SLUG}/research/candidates?format=long&limit=500").json()
    used = {c["video_id"] for c in candidates["candidates"] if c["used_before"]}
    assert MOCK_PICK in used and other not in used


def test_auto_modes_run_straight_through_to_the_images_review(api: TestClient) -> None:
    created = create(
        api,
        {"kind": "ai_pick"},
        stage_mode_overrides={
            "research": "auto", "title": "auto", "script": "auto", "storyboard": "auto"
        },
    )
    # Voice is auto by default, images is the first review gate on the way.
    project = wait_for(api, created["id"], at_gate("images", "awaiting_review"), timeout=120)
    assert statuses(project)["storyboard"] == "done" and statuses(project)["voice"] == "done"
    assert project["title"] and project["title"] != "AI pick (research pending)"
    folder = Path(project["folder"])
    assert (folder / "04_storyboard" / "storyboard.json").is_file()
    assert (folder / "03_script" / "script.md").is_file()
    for name in VOICE_FILES:
        assert (folder / "05_voice" / name).is_file(), name
    assert any((folder / "06_images").glob("scene_*.png"))
    assert (folder / "06_images" / "images.json").is_file()


@pytest.mark.skipif(not FFMPEG_READY, reason=NEEDS_FFMPEG)
def test_manual_voice_recording_gets_its_timing_when_the_run_continues(
    api: TestClient,
) -> None:
    """Voice set to manual: a person drops voice.wav into 05_voice and presses Run. The
    engine refuses without the file, and with it the voice stage builds timing.json (the
    clock) from the recording before the project moves on to the images."""
    created = create(
        api,
        {"kind": "ai_pick"},
        stage_mode_overrides={
            "research": "auto", "title": "auto", "script": "auto", "storyboard": "auto",
            "voice": "manual",
        },
    )
    project = wait_for(api, created["id"], at_gate("voice", "awaiting_manual"), timeout=120)
    folder = Path(project["folder"])
    refused = api.post(f"/api/projects/{created['id']}/run")
    assert refused.status_code == 409 and "voice.wav" in refused.json()["detail"]

    write_tone(folder / "05_voice" / "voice.wav", 12.0, hz=330.0)
    started = api.post(f"/api/projects/{created['id']}/run")
    assert started.status_code == 202, started.text
    project = wait_for(api, created["id"], at_gate("images", "awaiting_review"), timeout=120)
    assert statuses(project)["voice"] == "done"
    timing = read_json(folder / "05_voice" / "timing.json")
    assert timing["own_recording"] is True
    assert timing["source"] == "estimated" and timing["timing_confidence"] == "low"
    assert abs(timing["duration_s"] - 12.0) < 0.1
    assert timing["sentences"] and timing["sentences"][-1]["end_s"] <= 12.0 + 1e-6
    script = read_json(folder / "03_script" / "script.json")
    sentence_ids = [s["id"] for sec in script["sections"] for p in sec["paragraphs"]
                    for s in p["sentences"]]
    assert [s["id"] for s in timing["sentences"]] == sentence_ids
    assert (folder / "05_voice" / "voice.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/voice").json()
    assert payload["own_recording"] is True and payload["timing_confidence"] == "low"
    history = "\n".join(project["stages"]["voice"]["history"])
    assert "supplied by hand" in history
