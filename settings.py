"""插件配置。"""

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=20, ge=1, le=120, title="请求超时（秒）")
