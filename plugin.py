"""注册 JavBus 元数据来源：按番号提供影片元数据。"""

import json
from pathlib import Path

from src.plugins import (
    HOST_API_VERSION,
    METADATA_SOURCE_EXTENSION_KEY,
    PluginContext,
    PluginExtension,
    PluginMetadataSource,
    PluginRegistration,
)

from .actor_sync import JavbusActorMovieSource, JavbusActorStateStore
from .javbus import JavBusMetadataSource
from .rate_limit import RateLimiter, is_aps_process
from .settings import Settings
from .translations import text_map

PLUGIN_ID = "sakuramedia_javbus_metadata"
DISPLAY_NAME = "JavBus"
DISPLAY_NAME_I18N = text_map("manifest.display_name")
MANIFEST = json.loads(
    Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
)
VERSION = MANIFEST["version"]


def register(context: PluginContext) -> PluginRegistration:
    settings = Settings.model_validate(dict(context.settings))
    rate_limiter = (
        RateLimiter(settings.min_request_interval_seconds)
        if is_aps_process()
        else None
    )
    source = JavBusMetadataSource(
        data_dir=context.data_dir,
        timeout_seconds=settings.timeout_seconds,
        i18n=context.i18n,
        rate_limiter=rate_limiter,
    )
    actor_source = JavbusActorMovieSource(
        source=source,
        state_store=JavbusActorStateStore(
            context.data_dir / "javbus" / "actors.json"
        ),
    )
    return PluginRegistration(
        plugin_id=PLUGIN_ID,
        display_name=DISPLAY_NAME,
        display_name_i18n=DISPLAY_NAME_I18N,
        version=VERSION,
        host_api_version=HOST_API_VERSION,
        extensions=(
            PluginExtension(
                key=METADATA_SOURCE_EXTENSION_KEY,
                data=PluginMetadataSource(
                    fetch_movie=source.fetch_movie,
                    fetch_actor_movies=actor_source.fetch_actor_movies,
                ),
            ),
        ),
    )
