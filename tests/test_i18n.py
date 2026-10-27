from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from sakuramedia_javbus_metadata.javbus import (
    JavBusMetadataSource,
    JavBusPageError,
    parse_movie_page,
)
from sakuramedia_javbus_metadata.plugin import DISPLAY_NAME_I18N, PLUGIN_ID, register
from src.plugins import PluginContext
from src.plugins.i18n import PluginI18n

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (Path(__file__).parent / "data" / "ssis001.html").read_text(encoding="utf-8")
I18N = PluginI18n(PLUGIN_ID, PLUGIN_ROOT)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _catalog(locale: str) -> dict[str, str]:
    return json.loads(
        (PLUGIN_ROOT / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
    )


def test_catalogs_declare_same_keys_and_placeholders() -> None:
    zh = _catalog("zh-CN")
    en = _catalog("en")

    assert set(zh) == set(en)
    for key in zh:
        assert set(_PLACEHOLDER.findall(zh[key])) == set(
            _PLACEHOLDER.findall(en[key])
        )


def test_manifest_and_registration_declare_display_name_i18n(tmp_path: Path) -> None:
    manifest = json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["display_name_i18n"] == {"zh-CN": "JavBus", "en": "JavBus"}
    assert DISPLAY_NAME_I18N == manifest["display_name_i18n"]

    registration = register(
        PluginContext(plugin_id=PLUGIN_ID, settings={}, data_dir=tmp_path / "data")
    )
    assert registration.display_name_i18n == manifest["display_name_i18n"]


def test_parse_error_message_follows_request_language(set_language) -> None:
    broken_page = FIXTURE.replace("150分鐘", "未知")

    set_language("zh-CN")
    with pytest.raises(JavBusPageError) as zh_error:
        parse_movie_page(broken_page, I18N)

    set_language("en")
    with pytest.raises(JavBusPageError) as en_error:
        parse_movie_page(broken_page, I18N)

    assert zh_error.value.args[0] == (
        "JavBus 页面缺少发行日期或时长 movie_number=SSIS-001"
    )
    assert en_error.value.args[0] == (
        "JavBus page is missing the release date or duration "
        "(movie number SSIS-001)"
    )


def test_verification_error_message_follows_request_language(
    tmp_path: Path, set_language
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/doc/driver-verify"})

    source = JavBusMetadataSource(
        data_dir=tmp_path / "data", timeout_seconds=5, i18n=I18N
    )
    source.client.close()
    source.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )

    set_language("zh-CN")
    with pytest.raises(JavBusPageError) as zh_error:
        source.fetch_movie("SSIS-001")

    set_language("en")
    with pytest.raises(JavBusPageError) as en_error:
        source.fetch_movie("SSIS-001")

    assert zh_error.value.args[0] == "JavBus 要求人机验证，本次查询未执行"
    assert en_error.value.args[0] == (
        "JavBus requires human verification; this lookup was not performed"
    )
