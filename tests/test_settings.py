from __future__ import annotations

from sakuramedia_javbus_metadata.settings import Settings


def test_settings_schema_exposes_localized_timeout_title() -> None:
    prop = Settings.model_json_schema()["properties"]["timeout_seconds"]

    assert prop["title"] == "请求超时（秒）"
    assert prop["title_i18n"] == {
        "zh-CN": "请求超时（秒）",
        "en": "Request timeout (seconds)",
    }


def test_settings_default_timeout() -> None:
    assert Settings().timeout_seconds == 20
