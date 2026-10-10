"""The reference channel file loads into the Pydantic model and survives a JSON round trip."""

from __future__ import annotations

from pathlib import Path

import yaml

from cashcow_studio.models.channel import Channel, ChannelSummary
from cashcow_studio.storage.channel_store import make_slug, summary_of

EXAMPLE = Path(__file__).resolve().parents[1] / "channels" / "example-channel.yaml"


def load_example() -> Channel:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    # The YAML has no slug, exactly like a form submission; the app derives it from the name.
    data.setdefault("slug", make_slug(data["channel"]["name"]))
    return Channel.model_validate(data)


def test_example_yaml_loads_into_channel() -> None:
    channel = load_example()
    assert channel.slug == "kind-ledger"
    assert channel.channel.name == "Kind Ledger"
    assert channel.channel.language == "English"
    assert channel.channel.formats == "both"
    assert channel.channel.brand_colors == ["#1F3864", "#FFC000", "#FFFFFF"]
    assert channel.channel.browser_profile == 84
    assert [c.name for c in channel.competitors] == ["Human Ember", "The Gentle Hour"]
    assert channel.competitors[1].priority == 2
    assert [f.type for f in channel.frameworks] == ["title", "script_long"]
    assert channel.voice.tool == "ai33"
    assert channel.voice.clone_ref == "clone_123"
    assert channel.voice.api_key_env == "AI33_API_KEY"
    assert channel.voice.returns_word_timestamps is None
    assert channel.images.tool == "google_gemini"
    assert channel.images.on_image_text == "app_popups"
    assert channel.thumbnail.max_headline_words == 4
    assert channel.stage_modes.voice == "auto"
    assert channel.stage_modes.research == "review"
    assert channel.reviewer == "Imran"
    assert channel.schema_version == 1


def test_example_round_trips_through_json() -> None:
    channel = load_example()
    again = Channel.model_validate_json(channel.model_dump_json())
    assert again == channel


def test_summary_matches_contract_fields() -> None:
    summary = summary_of(load_example())
    assert isinstance(summary, ChannelSummary)
    assert summary.model_dump() == {
        "slug": "kind-ledger",
        "name": "Kind Ledger",
        "language": "English",
        "formats": "both",
        "status": "setup",
        "competitors": 2,
        "updated_at": None,
    }


def test_example_keys_match_model_top_level() -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    model_fields = set(Channel.model_fields)
    assert set(data) <= model_fields
    # Fields the app fills in are allowed to be absent from the example.
    assert model_fields - set(data) <= {"slug", "created_at", "updated_at", "schema_version"}
