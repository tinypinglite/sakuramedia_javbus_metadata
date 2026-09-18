"""注册 JavBus 元数据来源：JavDB 明确未收录时按番号补缺。"""

import json
from pathlib import Path

from src.plugins import (
    METADATA_SOURCE_EXTENSION_KEY,
    PluginContext,
    PluginExtension,
    PluginMetadataSource,
    PluginRegistration,
)

from .javbus import JavBusMetadataSource
from .settings import Settings

PLUGIN_ID = "sakuramedia_javbus_metadata"
DISPLAY_NAME = "JavBus"
MANIFEST = json.loads(
    Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
)
VERSION = MANIFEST["version"]
HOST_API_VERSION = MANIFEST["host_api_version"]


def register(context: PluginContext) -> PluginRegistration:
    settings = Settings.model_validate(dict(context.settings))
    source = JavBusMetadataSource(
        data_dir=context.data_dir,
        timeout_seconds=settings.timeout_seconds,
    )
    return PluginRegistration(
        plugin_id=PLUGIN_ID,
        display_name=DISPLAY_NAME,
        version=VERSION,
        host_api_version=HOST_API_VERSION,
        extensions=(
            PluginExtension(
                key=METADATA_SOURCE_EXTENSION_KEY,
                data=PluginMetadataSource(fetch_movie=source.fetch_movie),
            ),
        ),
    )
