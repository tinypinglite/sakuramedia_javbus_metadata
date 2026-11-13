from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from src.plugins.i18n import PluginI18n

from sakuramedia_javbus_metadata.javbus import (
    JavBusMetadataSource,
    JavBusPageError,
    parse_actor_movie_page,
    parse_actor_search_page,
    parse_movie_page,
)
from sakuramedia_javbus_metadata.plugin import PLUGIN_ID

FIXTURE = (Path(__file__).parent / "data" / "ssis001.html").read_text(encoding="utf-8")
EXPECTED_TITLE = (
    "一ヶ月間の禁欲の果てに彼女のルームメイト2人と浮気SEXだけに没頭した"
    "彼女不在の3日間。"
)
COVER_PATH = "/pics/cover/83ie_b.jpg"
I18N = PluginI18n(PLUGIN_ID, Path(__file__).resolve().parents[1])

ACTOR_SEARCH_HTML = """
<div id="waterfall">
  <div class="item">
    <a class="avatar-box text-center" href="https://www.javbus.com/star/2xi">
      <div class="photo-frame">
        <img src="/pics/actress/2xi_a.jpg" title="葵つかさ">
      </div>
      <div class="photo-info">
        <span class="mleft">葵つかさ<button>有碼</button></span>
      </div>
    </a>
  </div>
</div>
"""

ACTOR_MOVIES_P1 = """
<div id="waterfall">
  <div class="item">
    <a class="movie-box" href="https://www.javbus.com/ABC-001">
      <div class="photo-info">
        <span>title<br><div class="item-tag"></div>
        <date>ABC-001</date> / <date>2024-02-01</date></span>
      </div>
    </a>
  </div>
  <div class="item">
    <a class="movie-box" href="https://www.javbus.com/ABC-002">
      <div class="photo-info">
        <span>title<br><div class="item-tag"></div>
        <date>ABC-002</date> / <date>2024-01-15</date></span>
      </div>
    </a>
  </div>
</div>
<ul class="pagination"><li><a id="next" href="/star/2xi/2">下一頁</a></li></ul>
"""

ACTOR_MOVIES_P2 = """
<div id="waterfall">
  <div class="item">
    <a class="movie-box" href="https://www.javbus.com/ABC-003">
      <div class="photo-info">
        <span>title<br><div class="item-tag"></div>
        <date>ABC-003</date> / <date>2023-12-01</date></span>
      </div>
    </a>
  </div>
</div>
"""


def _build_source(tmp_path: Path, handler) -> JavBusMetadataSource:
    source = JavBusMetadataSource(
        data_dir=tmp_path / "data", timeout_seconds=5, i18n=I18N
    )
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
    parsed = parse_movie_page(FIXTURE, I18N)
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
        parse_movie_page(FIXTURE.replace("150分鐘", "未知"), I18N)


def test_parse_actor_search_page() -> None:
    actors = parse_actor_search_page(ACTOR_SEARCH_HTML)

    assert [(actor.star_id, actor.name) for actor in actors] == [
        ("2xi", "葵つかさ")
    ]
    assert parse_actor_search_page("<h4>沒有您要的結果！</h4>") == ()


def test_parse_actor_movie_page() -> None:
    page = parse_actor_movie_page(ACTOR_MOVIES_P1)

    assert [movie.movie_number for movie in page.movies] == ["ABC-001", "ABC-002"]
    assert [movie.release_date for movie in page.movies] == [
        date(2024, 2, 1),
        date(2024, 1, 15),
    ]
    assert page.has_next is True
    assert parse_actor_movie_page(ACTOR_MOVIES_P2).has_next is False


def test_source_resolves_actor_and_reads_actor_movies(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/searchstar/"):
            return httpx.Response(200, text=ACTOR_SEARCH_HTML)
        if request.url.path == "/star/2xi":
            assert "existmag=all" in request.headers["cookie"]
            return httpx.Response(200, text=ACTOR_MOVIES_P1)
        return httpx.Response(404)

    source = _build_source(tmp_path, handler)
    assert source.find_actors(
        name="葵つかさ", alias_names=["葵司"]
    ) == ("2xi",)
    page = source.get_actor_movies(star_id="2xi", page=1)
    assert page is not None
    assert page.movies[0].movie_number == "ABC-001"
    assert page.has_next is True


def test_source_keeps_every_star_page_with_same_name(tmp_path: Path) -> None:
    search_html = """
<div id="waterfall">
  <div class="item">
    <a class="avatar-box text-center" href="https://www.javbus.com/star/2xi">
      <img src="/pics/actress/2xi_a.jpg" title="葵つかさ">
      <span class="mleft">葵つかさ<button>有碼</button></span>
    </a>
  </div>
  <div class="item">
    <a class="avatar-box text-center" href="https://www.javbus.com/star/9kd">
      <img src="/pics/actress/9kd_a.jpg" title="葵つかさ">
      <span class="mleft">葵つかさ<button>有碼</button></span>
    </a>
  </div>
  <div class="item">
    <a class="avatar-box text-center" href="https://www.javbus.com/star/7zz">
      <img src="/pics/actress/7zz_a.jpg" title="同名不同后缀">
      <span class="mleft">同名不同后缀さん<button>無碼</button></span>
    </a>
  </div>
</div>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/searchstar/"):
            return httpx.Response(200, text=search_html)
        return httpx.Response(404)

    source = _build_source(tmp_path, handler)

    assert source.find_actors(
        name="葵つかさ", alias_names=[]
    ) == ("2xi", "9kd")


def test_parse_actor_movie_page_keeps_movies_with_invalid_date() -> None:
    html = """
<div id="waterfall">
  <div class="item">
    <a class="movie-box" href="https://www.javbus.com/ABC-001">
      <date>ABC-001</date> / <date>0000-00-00</date>
    </a>
  </div>
</div>
"""
    page = parse_actor_movie_page(html)

    assert [movie.movie_number for movie in page.movies] == ["ABC-001"]
    assert page.movies[0].release_date is None


def test_parse_actor_movie_page_strips_release_date_suffix() -> None:
    html = """
<div id="waterfall">
  <div class="item">
    <a class="movie-box" href="https://www.javbus.com/START-649_2026-10-13">
      <date>START-649</date> / <date>2026-10-13</date>
    </a>
  </div>
</div>
"""
    page = parse_actor_movie_page(html)

    assert [movie.movie_number for movie in page.movies] == ["START-649"]
    assert page.movies[0].release_date == date(2026, 10, 13)


def test_parse_movie_page_rejects_invalid_release_date() -> None:
    with pytest.raises(JavBusPageError):
        parse_movie_page(FIXTURE.replace("2021-02-18", "0000-00-00"), I18N)


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
