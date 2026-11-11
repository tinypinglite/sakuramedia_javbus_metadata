"""插件配置。"""

from pydantic import BaseModel, ConfigDict, Field

from .translations import text_map


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(
        default=20,
        ge=1,
        le=120,
        title="请求超时（秒）",
        json_schema_extra={
            "title_i18n": text_map("settings.timeout_seconds.title"),
        },
    )
    min_request_interval_seconds: float = Field(
        default=3.0,
        ge=0.5,
        le=60.0,
        title="APS 请求间隔（秒）",
        description="仅在 APS 进程内生效，默认 3 秒",
        json_schema_extra={
            "title_i18n": text_map(
                "settings.min_request_interval_seconds.title"
            ),
            "description_i18n": text_map(
                "settings.min_request_interval_seconds.description"
            ),
        },
    )
