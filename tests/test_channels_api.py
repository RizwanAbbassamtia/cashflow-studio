"""/api/channels: create, list, get, update, archive, conflicts, validation, uploads, URLs."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from conftest import AppEnv, channel_payload


def test_create_derives_slug_and_writes_file(client: TestClient, app_env: AppEnv) -> None:
    response = client.post("/api/channels", json=channel_payload("Kind Ledger"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "kind-ledger"
    assert body["channel"]["name"] == "Kind Ledger"
    assert body["channel"]["brand_colors"] == ["#1F3864", "#FFC000"]  # normalised by the model
    assert body["created_at"] is not None
    assert body["updated_at"] == body["created_at"]
    assert body["schema_version"] == 1

    file = app_env.channels_dir / "kind-ledger" / "channel.json"
    assert file.is_file()
    on_disk = json.loads(file.read_text(encoding="utf-8"))
    assert on_disk["slug"] == "kind-ledger"
    assert on_disk["voice"]["api_key_env"] == "FISH_AUDIO_API_KEY"


def test_create_accepts_explicit_slug(client: TestClient) -> None:
    response = client.post("/api/channels", json=channel_payload("Kind Ledger", slug="ledger-2"))
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "ledger-2"


def test_slug_conflict_returns_409(client: TestClient) -> None:
    assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
    response = client.post("/api/channels", json=channel_payload("Kind  Ledger"))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_list_is_sorted_by_name(client: TestClient) -> None:
    for name in ("Zebra Tales", "apple stories", "Mellow Minds"):
        assert client.post("/api/channels", json=channel_payload(name)).status_code == 201
    response = client.get("/api/channels")
    assert response.status_code == 200
    rows = response.json()
    assert [row["name"] for row in rows] == ["apple stories", "Mellow Minds", "Zebra Tales"]
    first = rows[0]
    assert set(first) == {
        "slug", "name", "language", "formats", "status", "competitors", "updated_at"
    }
    assert first["competitors"] == 2
    assert first["status"] == "setup"


def test_list_is_empty_when_nothing_exists(client: TestClient) -> None:
    assert client.get("/api/channels").json() == []


def test_get_returns_channel_and_404_when_missing(client: TestClient) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    response = client.get("/api/channels/kind-ledger")
    assert response.status_code == 200
    assert response.json()["channel"]["niche"] == "Kindness and emotional stories"

    missing = client.get("/api/channels/nobody-here")
    assert missing.status_code == 404
    assert "detail" in missing.json()

    bad_slug = client.get("/api/channels/Not%20A%20Slug")
    assert bad_slug.status_code == 404


def test_update_sets_updated_at_and_keeps_created_at(client: TestClient) -> None:
    created = client.post("/api/channels", json=channel_payload("Kind Ledger")).json()
    created["channel"]["status"] = "active"
    created["competitors"] = []
    created["updated_at"] = None  # the server decides this
    response = client.put("/api/channels/kind-ledger", json=created)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["channel"]["status"] == "active"
    assert updated["competitors"] == []
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] is not None
    assert updated["updated_at"] >= created["created_at"]
    assert client.get("/api/channels").json()[0]["status"] == "active"


def test_update_rejects_slug_mismatch_and_missing_channel(client: TestClient) -> None:
    created = client.post("/api/channels", json=channel_payload("Kind Ledger")).json()
    mismatch = client.put("/api/channels/other-slug", json=created)
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.json()["detail"]

    created["slug"] = "ghost-channel"
    missing = client.put("/api/channels/ghost-channel", json=created)
    assert missing.status_code == 404


def test_archive_moves_folder_and_never_deletes(client: TestClient, app_env: AppEnv) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    response = client.delete("/api/channels/kind-ledger")
    assert response.status_code == 204
    assert response.content == b""

    assert not (app_env.channels_dir / "kind-ledger").exists()
    archived = list((app_env.channels_dir / "_archived").iterdir())
    assert len(archived) == 1
    assert archived[0].name.startswith("kind-ledger-")
    assert (archived[0] / "channel.json").is_file()

    assert client.get("/api/channels/kind-ledger").status_code == 404
    assert client.get("/api/channels").json() == []  # _archived is not listed
    assert client.delete("/api/channels/kind-ledger").status_code == 404


def test_validation_error_returns_422_with_details(client: TestClient) -> None:
    bad_colour = channel_payload("Kind Ledger")
    bad_colour["channel"]["brand_colors"] = ["blue"]
    response = client.post("/api/channels", json=bad_colour)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("brand_colors" in str(item.get("loc")) for item in detail)

    no_name = channel_payload("Kind Ledger")
    no_name["channel"].pop("name")
    assert client.post("/api/channels", json=no_name).status_code == 422

    bad_url = channel_payload("Kind Ledger")
    bad_url["competitors"][0]["url"] = "not a url"
    assert client.post("/api/channels", json=bad_url).status_code == 422


def test_name_without_letters_cannot_become_a_slug(client: TestClient) -> None:
    response = client.post("/api/channels", json=channel_payload("!!!"))
    assert response.status_code == 422
    assert "letter or digit" in response.json()["detail"]


def test_framework_upload_saves_under_channel_folder(
    client: TestClient, app_env: AppEnv
) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    response = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("Title Writing Prompt.txt", b"Write ten titles...", "text/plain")},
        data={"type": "title"},
    )
    assert response.status_code == 200, response.text
    framework = response.json()
    assert framework["type"] == "title"
    assert framework["name"] == "Title Writing Prompt"
    assert framework["path"] == "channels/kind-ledger/frameworks/Title_Writing_Prompt.txt"
    saved = app_env.shared_dir / framework["path"]
    assert saved.read_bytes() == b"Write ten titles..."

    # A second file with the same name gets a numbered name instead of overwriting.
    again = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("Title Writing Prompt.txt", b"v2", "text/plain")},
        data={"type": "title"},
    )
    assert again.json()["path"].endswith("/Title_Writing_Prompt-2.txt")


def test_framework_upload_blocks_path_traversal(client: TestClient, app_env: AppEnv) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    response = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("../../../evil.txt", b"x", "text/plain")},
        data={"type": "other"},
    )
    assert response.status_code == 200, response.text
    path = response.json()["path"]
    assert path.startswith("channels/kind-ledger/frameworks/")
    assert ".." not in path
    assert (app_env.shared_dir / path).is_file()
    assert not (app_env.shared_dir.parent / "evil.txt").exists()


def test_framework_upload_validates_type_and_channel(client: TestClient) -> None:
    client.post("/api/channels", json=channel_payload("Kind Ledger"))
    bad_type = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"type": "recipe"},
    )
    assert bad_type.status_code == 422
    missing = client.post(
        "/api/channels/no-such/frameworks/upload",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"type": "title"},
    )
    assert missing.status_code == 404


def test_validate_url_classifies_links_without_network(client: TestClient) -> None:
    cases = {
        "https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg": (
            "channel", "https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg"
        ),
        "youtube.com/@thegentlehour/videos": ("channel", "https://www.youtube.com/@thegentlehour"),
        "https://www.youtube.com/c/SomeName": ("channel", "https://www.youtube.com/c/SomeName"),
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s": (
            "video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ),
        "https://youtu.be/dQw4w9WgXcQ": ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        "https://www.youtube.com/shorts/dQw4w9WgXcQ": (
            "video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ),
    }
    for url, (kind, normalized) in cases.items():
        response = client.get("/api/channels/validate-url", params={"url": url})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "kind": kind, "normalized": normalized}, url

    for url in ("https://example.com/watch?v=abc", "not a link", "https://www.youtube.com/"):
        body = client.get("/api/channels/validate-url", params={"url": url}).json()
        assert body["ok"] is False and body["kind"] == "unknown", url
