from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from sakuramedia_javbus_metadata.javbus import (
    JavBusMetadataSource,
    JavBusPageError,
    parse_movie_page,
)

FIXTURE = (Path(__file__).parent / "data" / "ssis001.html").read_text(encoding="utf-8")
EXPECTED_TITLE = (
    "一ヶ月間の禁欲の果てに彼女のルームメイト2人と浮気SEXだけに没頭した"
    "彼女不在の3日間。"
)
COVER_PATH = "/pics/cover/83ie_b.jpg"


def _build_source(tmp_path: Path, handler) -> JavBusMetadataSource:
    source = JavBusMetadataSource(data_dir=tmp_path / "data", timeout_seconds=5)
    source.client.close()
    source.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    return source


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == COVER_PATH:
        return httpx.Response(200, content=b"cover-bytes")
    if request.url.path == "/SSIS-001":
        return httpx.Response(200, text=FIXTURE)
    if request.url.host == "pics.dmm.co.jp":
        return httpx.Response(200, content=b"plot-bytes")
    return httpx.Response(404)


def test_parse_movie_page_maps_fields() -> None:
    parsed = parse_movie_page(FIXTURE)
    assert parsed is not None
    assert parsed.movie_number == "SSIS-001"
    assert parsed.title == EXPECTED_TITLE
    assert parsed.release_date == date(2021, 2, 18)
    assert parsed.duration_minutes == 150
    assert parsed.maker_name == "エスワン ナンバーワンスタイル"
    assert parsed.director_name == "苺原"
    assert parsed.series_name is None
    assert parsed.actors == ("葵つかさ", "乙白さやか")
    assert parsed.tags == (
        "多P",
        "美少女",
        "乳房",
        "薄馬賽克",
        "高畫質",
        "出軌",
        "戲劇",
        "DMM獨家",
    )
    assert parsed.cover_href == COVER_PATH
    assert len(parsed.plot_hrefs) == 10


def test_parse_movie_page_requires_date_and_duration() -> None:
    with pytest.raises(JavBusPageError):
        parse_movie_page(FIXTURE.replace("150分鐘", "未知"))


def test_fetch_movie_delivers_metadata_and_images(tmp_path: Path) -> None:
    source = _build_source(tmp_path, _ok_handler)
    metadata = source.fetch_movie("ssis-001")
    assert metadata is not None
    assert metadata.movie_number == "SSIS-001"
    assert metadata.title == EXPECTED_TITLE
    assert metadata.duration_minutes == 150
    assert metadata.maker_name == "エスワン ナンバーワンスタイル"
    assert [actor.name for actor in metadata.actors] == ["葵つかさ", "乙白さやか"]
    assert metadata.source_url == "https://www.javbus.com/SSIS-001"

    cover = Path(metadata.cover_image_path)
    assert cover.is_file()
    assert cover.read_bytes() == b"cover-bytes"
    assert len(metadata.plot_image_paths) == 10
    plot_paths = [Path(path) for path in metadata.plot_image_paths]
    assert all(path.is_file() for path in plot_paths)
    assert {path.parent for path in plot_paths} == {cover.parent}
    assert cover.parent.parent.name == "metadata-tmp"


def test_fetch_movie_returns_none_for_missing_movie(tmp_path: Path) -> None:
    source = _build_source(tmp_path, lambda request: httpx.Response(404))
    assert source.fetch_movie("ABCD-999") is None
    assert not (tmp_path / "data" / "metadata-tmp").exists()


def test_fetch_movie_returns_none_when_page_number_differs(tmp_path: Path) -> None:
    source = _build_source(tmp_path, lambda request: httpx.Response(200, text=FIXTURE))
    assert source.fetch_movie("ABCD-999") is None
    assert not (tmp_path / "data" / "metadata-tmp").exists()


def test_fetch_movie_raises_on_driver_verify(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/doc/driver-verify"})

    source = _build_source(tmp_path, handler)
    with pytest.raises(JavBusPageError):
        source.fetch_movie("SSIS-001")


def test_fetch_movie_cleans_up_delivery_when_cover_fails(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COVER_PATH:
            return httpx.Response(500)
        return httpx.Response(200, text=FIXTURE)

    source = _build_source(tmp_path, handler)
    with pytest.raises(httpx.HTTPStatusError):
        source.fetch_movie("SSIS-001")
    delivery_root = tmp_path / "data" / "metadata-tmp"
    assert not delivery_root.exists() or not list(delivery_root.iterdir())
