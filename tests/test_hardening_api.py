"""Hardening: no raw key in any error, bad values never reach ``.env``, writes from other
websites are refused, overlapping saves keep both changes, and every error is JSON."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cashflow_studio.app import create_app
from cashflow_studio.models.channel import Channel
from cashflow_studio.storage import channel_store
from cashflow_studio.storage.channel_store import ChannelExists, ChannelStore, safe_filename
from cashflow_studio.storage.settings_store import SettingsStore
from conftest import AppEnv, channel_payload

SECRET = "sk-ant-api03-REALSECRETVALUE-abcdefghijklmnop-WXYZ"


# Raw key values never leave the server, not even inside an error ---------------------------


def test_422_never_echoes_submitted_key_values(client: TestClient, app_env: AppEnv) -> None:
    # One mistyped name next to a correct one: the answer must not repeat either value.
    response = client.put(
        "/api/settings",
        json={"keys": {"anthropic_api_key": SECRET, "ANTHROPIC_API_KEY": SECRET}},
    )
    assert response.status_code == 422
    assert SECRET not in response.text
    assert "anthropic_api_key" in response.json()["detail"]

    # A pasted key in the name field is not repeated either.
    long_name = "sk-ant-" + "x" * 60
    response = client.put("/api/settings", json={"keys": {long_name: "value"}})
    assert response.status_code == 422
    assert long_name not in response.text

    # Pydantic errors (wrong type, keys not an object) carry type/loc/msg only.
    for body in ({"keys": {"FAL_KEY": 12345678}}, {"keys": [SECRET]}, {"keys": SECRET}):
        response = client.put("/api/settings", json=body)
        assert response.status_code == 422, body
        assert "12345678" not in response.text
        assert SECRET not in response.text
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail
        for item in detail:
            assert set(item) == {"type", "loc", "msg"}
    assert not app_env.env_file.exists()


@pytest.mark.parametrize(
    "value",
    [
        "ZQXabc\u0000defghijk",  # NUL: the environment refuses it
        "ZQX" + "x" * 40_000,  # longer than Windows allows for an environment entry
        "ZQXline1\nline2",
        "ZQXtab\there",
    ],
    ids=["nul", "oversized", "newline", "tab"],
)
def test_values_that_cannot_be_stored_are_rejected_before_writing(
    client: TestClient, app_env: AppEnv, value: str
) -> None:
    seeded = client.put("/api/settings", json={"keys": {"FAL_KEY": "fal-12345678"}})
    assert seeded.status_code == 200
    before = app_env.env_file.read_text(encoding="utf-8")

    response = client.put("/api/settings", json={"keys": {"GEMINI_API_KEY": value}})
    assert response.status_code == 422, response.status_code
    assert response.headers["content-type"].startswith("application/json")
    assert "GEMINI_API_KEY" in response.json()["detail"]
    assert "ZQX" not in response.text
    assert app_env.env_file.read_text(encoding="utf-8") == before
    assert os.environ.get("GEMINI_API_KEY") is None


def test_damaged_env_file_does_not_stop_the_app(app_env: AppEnv) -> None:
    app_env.app_data_dir.mkdir(parents=True, exist_ok=True)
    app_env.env_file.write_text(
        "FAL_KEY=abc\u0000defghijk\n"
        "GEMINI_API_KEY=" + "y" * 40_000 + "\n"
        "OPENAI_API_KEY=ok-value-12345678\n",
        encoding="utf-8",
    )
    with TestClient(create_app()) as client:  # load_settings() loads .env here
        body = client.get("/api/settings").json()
        assert body["keys"]["OPENAI_API_KEY"] == {"set": True, "masked": "ok-...5678"}
        assert os.environ["OPENAI_API_KEY"] == "ok-value-12345678"

        report = client.get("/api/doctor").json()
        image = next(check for check in report["checks"] if check["id"] == "image_key")
        assert image["status"] == "warn"
        assert "FAL_KEY" in image["detail"] and "GEMINI_API_KEY" in image["detail"]
        assert image["fix_hint"]
        assert "yyyy" not in json.dumps(report) and "defghijk" not in json.dumps(report)

        # The broken keys can be cleared from inside the app.
        cleared = client.put(
            "/api/settings", json={"keys": {"FAL_KEY": None, "GEMINI_API_KEY": None}}
        )
        assert cleared.status_code == 200
        text = app_env.env_file.read_text(encoding="utf-8")
        assert "\x00" not in text and "FAL_KEY" not in text and "OPENAI_API_KEY" in text


def test_app_and_system_settings_cannot_be_saved_as_keys(
    client: TestClient, app_env: AppEnv
) -> None:
    path_before = os.environ.get("PATH")
    for name in ("CFS_SHARED_DIR", "CFS_PORT", "PATH", "PYTHONPATH", "USERPROFILE"):
        response = client.put("/api/settings", json={"keys": {name: "abcdefghijkl"}})
        assert response.status_code == 422, name
        assert name in response.json()["detail"]
    assert os.environ.get("PATH") == path_before
    assert not app_env.env_file.exists()
    # Clearing such a line from a hand-edited .env still works and never unsets PATH itself.
    app_env.env_file.parent.mkdir(parents=True, exist_ok=True)
    app_env.env_file.write_text("CFS_PORT=9999\n", encoding="utf-8")
    cleared = client.put("/api/settings", json={"keys": {"CFS_PORT": None, "PATH": None}})
    assert cleared.status_code == 200
    assert "CFS_PORT" not in app_env.env_file.read_text(encoding="utf-8")
    assert os.environ.get("PATH") == path_before


# Writes must come from the app's own page ----------------------------------------------------


def test_writes_from_another_website_are_refused(client: TestClient, app_env: AppEnv) -> None:
    assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
    evil = {"Origin": "https://evil.example", "Referer": "https://evil.example/"}

    upload = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("payload.lnk", b"x", "application/octet-stream")},
        data={"type": "other"},
        headers=evil,
    )
    assert upload.status_code == 403
    assert "another website" in upload.json()["detail"]
    assert "access-control-allow-origin" not in upload.headers
    assert not (app_env.channels_dir / "kind-ledger" / "frameworks").exists()

    keys = client.put("/api/settings", json={"keys": {"FAL_KEY": "fal-12345678"}}, headers=evil)
    assert keys.status_code == 403
    assert not app_env.env_file.exists()

    assert client.delete("/api/channels/kind-ledger", headers=evil).status_code == 403
    assert client.get("/api/channels/kind-ledger").status_code == 200

    # "Origin: null" (sandboxed frames, file:// pages) is another site too.
    nulled = client.put(
        "/api/settings", json={"keys": {"FAL_KEY": "x"}}, headers={"Origin": "null"}
    )
    assert nulled.status_code == 403

    # Reads are not blocked here; CORS decides whether the page may see them.
    assert client.get("/api/channels", headers=evil).status_code == 200


def test_event_stream_from_another_website_is_refused(client: TestClient) -> None:
    """CORS does not cover WebSockets: a foreign page must not read the event stream."""
    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect("/api/ws", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert info.value.code == 1008
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers={"Origin": "null"}) as ws:
            ws.receive_json()
    # Without an Origin (tools on this PC) and from the dev server the stream works.
    with client.websocket_connect("/api/ws") as ws:
        ws.send_text("ping")
    with client.websocket_connect("/api/ws", headers={"Origin": "http://localhost:5173"}) as ws:
        ws.send_text("ping")


def test_event_stream_from_the_apps_own_page_passes(app_env: AppEnv) -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as client:
        # The test client always says Host: testserver on a handshake; the app's page sends
        # its own loopback host, which is what the guard compares the Origin with.
        with client.websocket_connect(
            "/api/ws", headers={"Origin": "http://127.0.0.1:8765", "Host": "127.0.0.1:8765"}
        ) as ws:
            ws.send_text("ping")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/ws",
                headers={"Origin": "http://evil.example:8765", "Host": "evil.example:8765"},
            ) as ws:
                ws.receive_json()


def test_writes_from_the_apps_own_page_and_the_dev_server_pass(app_env: AppEnv) -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as client:
        own = client.put(
            "/api/settings",
            json={"keys": {"FAL_KEY": "fal-12345678"}},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert own.status_code == 200, own.text

        dev = client.put(
            "/api/settings",
            json={"keys": {"FAL_KEY": "fal-12345678"}},
            headers={"Origin": "http://localhost:5173"},
        )
        assert dev.status_code == 200
        assert dev.headers.get("access-control-allow-origin") == "http://localhost:5173"

        # A name that merely resolves to this PC (DNS rebinding) is not this server.
        rebound = client.put(
            "/api/settings",
            json={"keys": {"FAL_KEY": "fal-12345678"}},
            headers={"Origin": "http://evil.example:8765", "Host": "evil.example:8765"},
        )
        assert rebound.status_code == 403


# Overlapping saves ------------------------------------------------------------------------


def _wait_briefly(barrier: threading.Barrier) -> None:
    try:
        barrier.wait(timeout=0.5)
    except threading.BrokenBarrierError:
        pass


def test_overlapping_key_saves_keep_both_changes(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two saves that read ``.env`` at the same moment must not lose each other's key.

    ``read_keys`` is slowed with a barrier: without the lock both threads read the empty file
    together and the second write wins; with the lock the second thread cannot even start
    reading until the first has written.
    """
    store = SettingsStore(app_env.app_data_dir)
    barrier = threading.Barrier(2)
    original = SettingsStore.read_keys

    def read_then_wait(self: SettingsStore) -> dict[str, str]:
        keys = original(self)
        _wait_briefly(barrier)
        return keys

    monkeypatch.setattr(SettingsStore, "read_keys", read_then_wait)
    errors: list[Exception] = []

    def save(name: str) -> None:
        try:
            store.update_keys({name: f"{name.lower()}-12345678"})
        except Exception as exc:  # pragma: no cover - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(n,)) for n in ("ANTHROPIC_API_KEY", "FAL_KEY")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    assert set(original(store)) == {"ANTHROPIC_API_KEY", "FAL_KEY"}


def test_overlapping_creates_of_one_channel_give_one_conflict(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ChannelStore(app_env.shared_dir)
    barrier = threading.Barrier(2)
    original = ChannelStore.exists

    def exists_then_wait(self: ChannelStore, slug: str) -> bool:
        found = original(self, slug)
        _wait_briefly(barrier)
        return found

    monkeypatch.setattr(ChannelStore, "exists", exists_then_wait)
    channel = Channel.model_validate(channel_payload("Kind Ledger") | {"slug": "kind-ledger"})
    outcomes: list[str] = []

    def create() -> None:
        try:
            store.create(channel)
            outcomes.append("created")
        except ChannelExists:
            outcomes.append("conflict")

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["conflict", "created"]


# Every error is JSON with a plain-English detail ---------------------------------------------


def test_channel_save_failure_is_a_json_500_with_a_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(channel_store, "atomic_write_text", refuse)
    response = client.post("/api/channels", json=channel_payload("Kind Ledger"))
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "could not be saved" in response.json()["detail"]


def test_unexpected_errors_are_json_not_plain_text(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: ChannelStore) -> list[object]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ChannelStore, "list", explode)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/channels")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"].startswith("Something went wrong")
    assert "disk on fire" not in response.text


def test_spa_route_survives_a_bad_address(
    app_env: AppEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    index = "<!doctype html><title>Cashflow Studio</title>"
    (dist / "index.html").write_text(index, encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("CFS_FRONTEND_DIST", str(dist))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/app.js").text == "console.log(1)"
        for path in ("/%00", "/channels/%00x", "/" + "a" * 300):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "Cashflow Studio" in response.text
        assert client.get("/api/nothing-here").status_code == 404


# File names ------------------------------------------------------------------------------


def test_safe_filename_handles_windows_device_names_and_long_extensions() -> None:
    assert safe_filename("CON") == "_CON"
    assert safe_filename("nul.txt") == "_nul.txt"
    assert safe_filename("com1.tar.gz") == "_com1.tar.gz"
    assert safe_filename("console.txt") == "console.txt"  # only the exact device names
    assert safe_filename("a" * 200 + ".pdf") == "a" * 80 + ".pdf"
    assert safe_filename("notes." + "x" * 40) == ("notes." + "x" * 40)[:80]
    assert safe_filename("Title Writing Prompt.txt") == "Title_Writing_Prompt.txt"


def test_framework_upload_with_a_device_name_is_renamed(
    client: TestClient, app_env: AppEnv
) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    response = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("CON.txt", b"x", "text/plain")},
        data={"type": "other"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"].endswith("/_CON.txt")
    assert (app_env.shared_dir / response.json()["path"]).read_bytes() == b"x"
