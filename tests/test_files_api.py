"""``GET /api/projects/{id}/files/{path}`` (content types, Range requests, traversal refused)
and ``POST /api/projects/{id}/upload/{stage}`` (safe names, stage folders, bad types)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cashcow_studio.api.files import resolve_project_file, safe_upload_name
from cashcow_studio.app import create_app
from cashcow_studio.models.project import ProjectCreate, ProjectSource
from conftest import AppEnv, channel_payload

SLUG = "kind-ledger"


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    with TestClient(create_app()) as client:
        assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
        yield client


@pytest.fixture
def project(api: TestClient) -> tuple[str, Path]:
    """A project folder without a running pipeline (created straight on the engine)."""
    engine = api.app.state.engine
    channel = engine.channels.get(SLUG)
    created = engine.create_project(
        ProjectCreate(channel_slug=SLUG, format="long",
                      source=ProjectSource(kind="own_topic", topic_text="Files test")),
        channel,
    )
    return created.id, Path(created.folder)


def write(folder: Path, relative: str, data: bytes) -> Path:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# Serving --------------------------------------------------------------------------------------


def test_serves_files_with_content_types(api: TestClient, project: tuple[str, Path]) -> None:
    project_id, folder = project
    write(folder, "05_voice/voice.wav", b"RIFF" + bytes(96))
    write(folder, "07_edit/proxy.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(50))
    write(folder, "06_images/scene_01.png", b"\x89PNG\r\n\x1a\n" + bytes(20))
    write(folder, "01_research/competitor_thumbnail.jpg", b"\xff\xd8\xff" + bytes(20))
    write(folder, "05_voice/timing.json", b'{"sample_rate": 48000}')
    write(folder, "07_edit/render.log", b"all good")
    write(folder, "08_export/weird.bin", b"xyz")

    cases = {
        "05_voice/voice.wav": "audio/wav",
        "07_edit/proxy.mp4": "video/mp4",
        "06_images/scene_01.png": "image/png",
        "01_research/competitor_thumbnail.jpg": "image/jpeg",
        "05_voice/timing.json": "application/json",
        "07_edit/render.log": "text/plain; charset=utf-8",
        "08_export/weird.bin": "application/octet-stream",
    }
    for relative, content_type in cases.items():
        response = api.get(f"/api/projects/{project_id}/files/{relative}")
        assert response.status_code == 200, relative
        assert response.headers["content-type"] == content_type, relative
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == (folder / relative).read_bytes()
        assert int(response.headers["content-length"]) == len(response.content)
    assert api.get(f"/api/projects/{project_id}/files/05_voice/timing.json").json() == {
        "sample_rate": 48000
    }
    head = api.head(f"/api/projects/{project_id}/files/05_voice/voice.wav")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == "100"


def test_range_requests(api: TestClient, project: tuple[str, Path]) -> None:
    project_id, folder = project
    data = bytes(range(256)) * 4  # 1024 bytes
    write(folder, "05_voice/voice.wav", data)
    url = f"/api/projects/{project_id}/files/05_voice/voice.wav"

    first = api.get(url, headers={"Range": "bytes=0-99"})
    assert first.status_code == 206
    assert first.content == data[:100]
    assert first.headers["content-range"] == "bytes 0-99/1024"
    assert first.headers["content-length"] == "100"
    assert first.headers["content-type"] == "audio/wav"

    middle = api.get(url, headers={"Range": "bytes=500-"})
    assert middle.status_code == 206 and middle.content == data[500:]
    assert middle.headers["content-range"] == "bytes 500-1023/1024"

    tail = api.get(url, headers={"Range": "bytes=-24"})
    assert tail.status_code == 206 and tail.content == data[-24:]

    beyond = api.get(url, headers={"Range": "bytes=5000-6000"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == "bytes */1024"

    video = write(folder, "07_edit/proxy.mp4", data)
    assert video.is_file()
    clip = api.get(f"/api/projects/{project_id}/files/07_edit/proxy.mp4",
                   headers={"Range": "bytes=10-19"})
    assert clip.status_code == 206 and clip.content == data[10:20]
    assert clip.headers["content-type"] == "video/mp4"


def test_refuses_paths_outside_the_project(api: TestClient, project: tuple[str, Path]) -> None:
    project_id, folder = project
    secret = folder.parent / "secret.txt"
    secret.write_text("not yours", encoding="utf-8")
    write(folder, "05_voice/voice.wav", b"RIFF")
    attempts = [
        "../secret.txt",
        "05_voice/../../secret.txt",
        "..%2Fsecret.txt",
        "%2e%2e/secret.txt",
        "05_voice/..\\..\\secret.txt",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "//server/share/file",
        "05_voice/voice.wav:stream",
    ]
    for attempt in attempts:
        response = api.get(f"/api/projects/{project_id}/files/{attempt}")
        assert response.status_code == 404, attempt
        assert "not yours" not in response.text
    # Folders and missing files are 404 too, and the folder's own job.json is served.
    assert api.get(f"/api/projects/{project_id}/files/05_voice").status_code == 404
    assert api.get(f"/api/projects/{project_id}/files/05_voice/nope.wav").status_code == 404
    assert api.get(f"/api/projects/{project_id}/files/").status_code == 404
    assert api.get(f"/api/projects/{project_id}/files/job.json").status_code == 200
    assert api.get("/api/projects/no-such-project/files/job.json").status_code == 404


def test_resolve_project_file_unit(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    write(folder, "a/b.txt", b"x")
    (tmp_path / "outside.txt").write_text("o", encoding="utf-8")
    assert resolve_project_file(folder, "a/b.txt") == (folder / "a" / "b.txt").resolve()
    assert resolve_project_file(folder, "a\\b.txt") == (folder / "a" / "b.txt").resolve()
    assert resolve_project_file(folder, "./a/./b.txt") == (folder / "a" / "b.txt").resolve()
    for bad in ("../outside.txt", "/outside.txt", "a/../../outside.txt", "", "a",
                str(tmp_path / "outside.txt"), "a/b.txt\x00"):
        with pytest.raises(HTTPException) as excinfo:
            resolve_project_file(folder, bad)
        assert excinfo.value.status_code == 404, bad


# Upload -----------------------------------------------------------------------------------------


def upload(api: TestClient, project_id: str, stage: str, name: str, data: bytes,
           scene: int | None = None, content_type: str = "application/octet-stream") -> object:
    form = {"scene": str(scene)} if scene is not None else {}
    return api.post(
        f"/api/projects/{project_id}/upload/{stage}",
        files={"file": (name, io.BytesIO(data), content_type)},
        data=form,
    )


def test_upload_saves_into_the_stage_folder(api: TestClient, project: tuple[str, Path]) -> None:
    project_id, folder = project
    response = upload(api, project_id, "voice", "My Take (final).WAV", b"RIFF" + bytes(40))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "05_voice/uploads/my-take-final.wav"
    assert body["stage"] == "voice" and body["size_bytes"] == 44 and body["scene"] is None
    saved = folder / "05_voice" / "uploads" / "my-take-final.wav"
    assert saved.read_bytes() == b"RIFF" + bytes(40)
    # The uploaded file is served back by the files endpoint.
    served = api.get(f"/api/projects/{project_id}/files/{body['path']}")
    assert served.status_code == 200 and served.headers["content-type"] == "audio/wav"
    # A second upload with the same name does not overwrite the first.
    again = upload(api, project_id, "voice", "My Take (final).WAV", b"RIFF2").json()
    assert again["path"] == "05_voice/uploads/my-take-final-2.wav"
    # Images carry the scene number in the name.
    image = upload(api, project_id, "images", "../../evil/photo.PNG", b"\x89PNG", scene=3).json()
    assert image["path"] == "06_images/uploads/scene_03_photo.png" and image["scene"] == 3
    assert (folder / "06_images" / "uploads" / "scene_03_photo.png").is_file()
    assert not (folder.parent / "evil").exists()
    nameless = upload(api, project_id, "export", ".mp4", b"\x00" * 8).json()
    assert nameless["path"] == "08_export/uploads/upload.mp4"
    assert not list((folder / "05_voice" / "uploads").glob("*.part"))


def test_upload_rejections(api: TestClient, project: tuple[str, Path]) -> None:
    project_id, _folder = project
    wrong_type = upload(api, project_id, "voice", "notes.txt", b"hello")
    assert wrong_type.status_code == 415 and "wav" in wrong_type.json()["detail"]
    wrong_image = upload(api, project_id, "images", "clip.mp4", b"x")
    assert wrong_image.status_code == 415
    empty = upload(api, project_id, "voice", "silent.wav", b"")
    assert empty.status_code == 422 and "empty" in empty.json()["detail"]
    stage = upload(api, project_id, "title", "x.wav", b"RIFF")
    assert stage.status_code == 404 and "voice, images, edit or export" in stage.json()["detail"]
    negative = upload(api, project_id, "images", "a.png", b"\x89PNG", scene=-1)
    assert negative.status_code == 422
    missing = upload(api, "no-such-project", "voice", "a.wav", b"RIFF")
    assert missing.status_code == 404
    no_file = api.post(f"/api/projects/{project_id}/upload/voice", data={"scene": "1"})
    assert no_file.status_code == 422


def test_safe_upload_name_unit() -> None:
    assert safe_upload_name("Take 1.MP3", "voice", None) == "take-1.mp3"
    assert safe_upload_name("C:\\Users\\me\\scene.jpg", "images", 7) == "scene_07_scene.jpg"
    assert safe_upload_name("x" * 200 + ".wav", "voice", None).endswith(".wav")
    assert len(safe_upload_name("x" * 200 + ".wav", "voice", None)) <= 64
    with pytest.raises(HTTPException) as excinfo:
        safe_upload_name("danger.exe", "voice", None)
    assert excinfo.value.status_code == 415
