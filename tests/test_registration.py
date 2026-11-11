from __future__ import annotations

import json
from pathlib import Path

from src.plugins import HOST_API_VERSION, PluginContext
from src.plugins.extensions.metadata import (
    METADATA_SOURCE_EXTENSION_KEY,
    METADATA_SOURCE_HOST_API_VERSION,
    validate_metadata_extension,
)
from src.plugins.loader import load_plugin_settings_model

from sakuramedia_javbus_metadata.plugin import DISPLAY_NAME, PLUGIN_ID, register


def test_settings_model_resolves_from_package_root() -> None:
    model = load_plugin_settings_model(Path(__file__).parents[1])
    assert model is not None
    assert "timeout_seconds" in model.model_fields


def test_registration_declares_metadata_source(tmp_path: Path) -> None:
    manifest = json.loads(
        (Path(__file__).parents[1] / "manifest.json").read_text(encoding="utf-8")
    )
    registration = register(
        PluginContext(plugin_id=PLUGIN_ID, settings={}, data_dir=tmp_path / "data")
    )
    assert manifest["plugin_id"] == PLUGIN_ID
    assert manifest["display_name"] == DISPLAY_NAME
    assert manifest["settings_model"] == "Settings"
    assert (
        METADATA_SOURCE_HOST_API_VERSION
        <= manifest["host_api_version"]
        <= HOST_API_VERSION
    )
    assert registration.plugin_id == PLUGIN_ID
    assert registration.host_api_version in {
        manifest["host_api_version"],
        HOST_API_VERSION,
    }
    assert [extension.key for extension in registration.extensions] == [
        METADATA_SOURCE_EXTENSION_KEY
    ]
    source = validate_metadata_extension(
        plugin_id=PLUGIN_ID, extension=registration.extensions[0]
    )
    assert callable(source.fetch_movie)
    assert callable(source.fetch_actor_movies)
