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
