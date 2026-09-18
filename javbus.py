"""JavBus 详情页解析与元数据交付（只按番号查询，不保存登录态）。"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from src.plugins import PluginMetadataActor, PluginMovieMetadata

BASE_URL = "https://www.javbus.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Cookie": "dv=1",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DURATION = re.compile(r"(\d+)\s*分鐘")


class JavBusPageError(RuntimeError):
    """页面可达但缺少必填字段、番号对不上或站点要求人机验证。"""


@dataclass(frozen=True)
class ParsedMovie:
    movie_number: str
    title: str
    release_date: date
    duration_minutes: int
    maker_name: str | None
    director_name: str | None
    series_name: str | None
    actors: tuple[str, ...]
    tags: tuple[str, ...]
    cover_href: str
    plot_hrefs: tuple[str, ...]


def _squash(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _clean_title(heading: str, movie_number: str, actors: tuple[str, ...]) -> str:
    title = heading.strip()
    if title.upper().startswith(movie_number.upper()):
        title = title[len(movie_number) :].strip()
    for name in reversed(actors):
        if title.endswith(name):
            title = title[: -len(name)].rstrip()
    return title or heading.strip()


def _suffix(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in _IMAGE_SUFFIXES else ".jpg"


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading_parts: list[str] = []
        self.info: dict[str, str] = {}
        self.actors: list[str] = []
        self.tags: list[str] = []
        self.cover_href: str | None = None
        self.plot_hrefs: list[str] = []
        self._in_heading = False
        self._in_header_span = False
        self._header_parts: list[str] = []
        self._value_parts: list[str] = []
        self._info_key: str | None = None
        self._in_star = False
        self._star_parts: list[str] = []
        self._in_genre = False
        self._genre_parts: list[str] = []
        self._genre_has_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if (
            self._in_genre
            and tag == "a"
            and "/genre/" in (attributes.get("href") or "")
        ):
            self._genre_has_link = True
        if tag == "h3":
            self._in_heading = True
        elif tag == "span" and "header" in classes:
            self._in_header_span = True
            self._header_parts = []
        elif tag == "span" and "genre" in classes:
            self._in_genre = True
            self._genre_parts = []
            self._genre_has_link = False
        elif tag == "a" and "bigImage" in classes and self.cover_href is None:
            self.cover_href = attributes.get("href")
        elif tag == "a" and "sample-box" in classes:
            href = attributes.get("href")
            if href:
                self.plot_hrefs.append(href)
        elif tag == "div" and "star-name" in classes:
            self._in_star = True
            self._star_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_heading = False
        elif tag == "span":
            if self._in_header_span:
                self._in_header_span = False
                self._info_key = _squash(self._header_parts).rstrip(":") or None
                self._value_parts = []
            elif self._in_genre:
                self._in_genre = False
                name = _squash(self._genre_parts)
                if name and self._genre_has_link:
                    self.tags.append(name)
        elif tag == "p":
            value = _squash(self._value_parts)
            if self._info_key and value:
                self.info[self._info_key] = value
            self._info_key = None
            self._value_parts = []
        elif tag == "div" and self._in_star:
            self._in_star = False
            name = _squash(self._star_parts)
            if name:
                self.actors.append(name)

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self.heading_parts.append(data)
        if self._in_star:
            self._star_parts.append(data)
        if self._in_genre:
            self._genre_parts.append(data)
        if self._in_header_span:
            self._header_parts.append(data)
        elif self._info_key is not None:
            self._value_parts.append(data)


def parse_movie_page(html: str) -> ParsedMovie | None:
    """解析影片详情页；页面不是影片页返回 None，缺少必填字段抛错。"""
    parser = _PageParser()
    parser.feed(html)
    parser.close()

    heading = _squash(parser.heading_parts)
    movie_number = parser.info.get("識別碼", "")
    if not movie_number and not heading:
        return None
    if not movie_number:
        raise JavBusPageError("JavBus 页面缺少識別碼")
    if not parser.cover_href:
        raise JavBusPageError(f"JavBus 页面缺少封面 movie_number={movie_number}")

    release_date_text = _DATE.search(parser.info.get("發行日期", ""))
    duration_match = _DURATION.search(parser.info.get("長度", ""))
    duration_minutes = int(duration_match.group(1)) if duration_match else 0
    if release_date_text is None or duration_minutes <= 0:
        raise JavBusPageError(
            f"JavBus 页面缺少发行日期或时长 movie_number={movie_number}"
        )

    actors = tuple(dict.fromkeys(parser.actors))
    return ParsedMovie(
        movie_number=movie_number,
        title=_clean_title(heading, movie_number, actors),
        release_date=date.fromisoformat(release_date_text.group(0)),
        duration_minutes=duration_minutes,
        maker_name=parser.info.get("製作商"),
        director_name=parser.info.get("導演"),
        series_name=parser.info.get("系列"),
        actors=actors,
        tags=tuple(dict.fromkeys(parser.tags)),
        cover_href=parser.cover_href,
        plot_hrefs=tuple(parser.plot_hrefs),
    )


def _requires_verification(response: httpx.Response) -> bool:
    location = response.headers.get("location", "")
    return (
        "/doc/driver-verify" in response.url.path
        or "/doc/driver-verify" in urlsplit(location).path
        or "Age Verification JavBus" in response.text
    )


class JavBusMetadataSource:
    """按番号查询 JavBus，并把封面与剧照交付到 metadata-tmp 请求目录。"""

    def __init__(self, *, data_dir: Path, timeout_seconds: float) -> None:
        self.data_dir = data_dir
        self.client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def fetch_movie(self, movie_number: str) -> PluginMovieMetadata | None:
        requested = (movie_number or "").strip()
        if not requested:
            return None
        detail_url = f"{BASE_URL}/{requested.upper()}"
        response = self.client.get(detail_url, headers=_HEADERS, follow_redirects=False)
        if _requires_verification(response):
            raise JavBusPageError("JavBus 要求人机验证，本次查询未执行")
        if response.status_code == 404:
            return None
        response.raise_for_status()

        parsed = parse_movie_page(response.text)
        if parsed is None or _compact(parsed.movie_number) != _compact(requested):
            return None

        request_dir = self.data_dir / "metadata-tmp" / uuid4().hex
        try:
            request_dir.mkdir(parents=True)
            details_url = str(response.url)
            cover_path = self._download_image(
                parsed.cover_href,
                request_dir / f"cover{_suffix(parsed.cover_href)}",
                details_url,
            )
            plot_paths: list[Path] = []
            for index, href in enumerate(parsed.plot_hrefs, start=1):
                target = request_dir / f"plot-{index:02d}{_suffix(href)}"
                try:
                    plot_paths.append(self._download_image(href, target, details_url))
                except (httpx.HTTPError, OSError):
                    target.unlink(missing_ok=True)
            return PluginMovieMetadata(
                movie_number=parsed.movie_number,
                title=parsed.title,
                release_date=parsed.release_date,
                duration_minutes=parsed.duration_minutes,
                cover_image_path=str(cover_path),
                summary="",
                maker_name=parsed.maker_name,
                director_name=parsed.director_name,
                series_name=parsed.series_name,
                actors=[PluginMetadataActor(name=name) for name in parsed.actors],
                tags=list(parsed.tags),
                plot_image_paths=[str(path) for path in plot_paths],
                source_url=details_url,
            )
        except Exception:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise

    def _download_image(self, url: str, target: Path, referer: str) -> Path:
        response = self.client.get(
            urljoin(BASE_URL, url),
            headers={**_HEADERS, "Referer": referer},
            follow_redirects=True,
        )
        response.raise_for_status()
        target.write_bytes(response.content)
        return target
