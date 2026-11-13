"""订阅演员影片列表：插件自管首次全量与后续增量状态。

同一演员在 JavBus 上可能被拆成多个 star 页面（有码/无码、历史拆页），
检索命中多个页面时全部合并，按页面顺序依次翻完。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from src.plugins import (
    PluginActorMoviePage,
    PluginActorMovieQuery,
    PluginActorRef,
)

from .javbus import (
    JavBusMetadataSource,
    JavBusPageError,
    ParsedActorMoviePage,
)
from .state import exclusive_file_lock, read_json, write_json_atomic
from .utils import parse_date

STATE_VERSION = 2
SUPPORTED_STATE_VERSIONS = {1, STATE_VERSION}
INCREMENTAL_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ActorSyncState:
    fingerprint: str
    source_actor_ids: tuple[str, ...] = ()
    full_synced_at: float | None = None
    last_synced_at: float | None = None
    latest_release_date: str | None = None
    pending_latest_release_date: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ActorSyncState | None:
        if not isinstance(payload, dict):
            return None
        fingerprint = payload.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        source_actor_ids = _source_actor_ids(payload)
        if source_actor_ids is None:
            return None
        return cls(
            fingerprint=fingerprint,
            source_actor_ids=source_actor_ids,
            full_synced_at=_optional_float(payload.get("full_synced_at")),
            last_synced_at=_optional_float(payload.get("last_synced_at")),
            latest_release_date=_optional_iso_date(
                payload.get("latest_release_date")
            ),
            pending_latest_release_date=_optional_iso_date(
                payload.get("pending_latest_release_date")
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "source_actor_ids": list(self.source_actor_ids),
            "full_synced_at": self.full_synced_at,
            "last_synced_at": self.last_synced_at,
            "latest_release_date": self.latest_release_date,
            "pending_latest_release_date": self.pending_latest_release_date,
        }


def _source_actor_ids(payload: dict[str, Any]) -> tuple[str, ...] | None:
    """读取 star 列表，兼容 v1 的单值字段 ``source_actor_id``。"""
    values = payload.get("source_actor_ids")
    if values is None:
        legacy = payload.get("source_actor_id")
        values = [legacy] if isinstance(legacy, str) and legacy else []
    if not isinstance(values, list):
        return None
    if any(not isinstance(value, str) or not value for value in values):
        return None
    return tuple(dict.fromkeys(values))


def _optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def actor_fingerprint(actor: PluginActorRef) -> str:
    aliases = sorted(
        {
            name.strip().casefold()
            for name in actor.alias_names
            if name and name.strip()
        }
    )
    payload = {
        "name": actor.name.strip().casefold(),
        "aliases": aliases,
        "gender": actor.gender,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JavbusActorStateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")
        self._thread_lock = threading.RLock()

    def get(self, actor_id: int) -> ActorSyncState | None:
        payload = read_json(self.path)
        actors = self._actors(payload)
        return ActorSyncState.from_payload(actors.get(str(actor_id)))

    def save(self, actor_id: int, state: ActorSyncState) -> None:
        with self._thread_lock:
            with exclusive_file_lock(self.lock_path):
                payload = read_json(self.path)
                actors = self._actors(payload)
                actors[str(actor_id)] = state.to_payload()
                write_json_atomic(
                    self.path,
                    {
                        "version": STATE_VERSION,
                        "actors": actors,
                    },
                )

    @staticmethod
    def _actors(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        if payload.get("version") not in SUPPORTED_STATE_VERSIONS:
            return {}
        actors = payload.get("actors")
        return dict(actors) if isinstance(actors, dict) else {}


class JavbusActorMovieSource:
    """把 JavBus 演员页转成宿主的分页结果。"""

    def __init__(
        self,
        *,
        source: JavBusMetadataSource,
        state_store: JavbusActorStateStore,
        clock: Callable[[], float] = time.time,
    ):
        self.source = source
        self.state_store = state_store
        self._clock = clock

    def fetch_actor_movies(
        self, query: PluginActorMovieQuery
    ) -> PluginActorMoviePage | None:
        actor = query.actor
        fingerprint = actor_fingerprint(actor)
        state = self._load_state(actor, fingerprint)
        start = query.cursor is None
        if start:
            state = replace(
                state,
                pending_latest_release_date=None,
                full_synced_at=(
                    None if query.mode == "full" else state.full_synced_at
                ),
            )
            self.state_store.save(actor.actor_id, state)
            logger.info(
                "JavBus actor movies start actor_id={} actor_name={} mode={} "
                "full_synced={} latest_release_date={}",
                actor.actor_id,
                actor.name,
                query.mode,
                state.full_synced_at is not None,
                state.latest_release_date,
            )

        state = self._ensure_star_ids(actor, state)
        if state is None:
            return None
        star_ids = state.source_actor_ids
        star_index, page = self._cursor_position(query.cursor, len(star_ids))

        retried = False
        while True:
            page_data, star_index, page = self._fetch_available_page(
                actor=actor,
                star_ids=star_ids,
                star_index=star_index,
                page=page,
                mode=query.mode,
            )
            if page_data is not None:
                break
            if retried or not start:
                logger.warning(
                    "JavBus actor movies page unavailable actor_id={} "
                    "actor_name={} star_ids={}",
                    actor.actor_id,
                    actor.name,
                    list(star_ids),
                )
                return None
            retried = True
            logger.warning(
                "JavBus actor movies page unavailable, re-resolving actor "
                "actor_id={} actor_name={} star_ids={}",
                actor.actor_id,
                actor.name,
                list(star_ids),
            )
            state = ActorSyncState(fingerprint=fingerprint)
            state = self._ensure_star_ids(actor, state)
            if state is None:
                return None
            star_ids = state.source_actor_ids
            star_index, page = 0, 1

        # 重新解析会把状态清空，全量标记要按重解析后的状态判断。
        full = query.mode == "full" or state.full_synced_at is None
        logger.info(
            "JavBus actor movies page fetched actor_id={} actor_name={} "
            "page={} movies={} has_next={}",
            actor.actor_id,
            actor.name,
            page,
            len(page_data.movies),
            page_data.has_next,
        )
        page_dates = [
            movie.release_date
            for movie in page_data.movies
            if movie.release_date is not None
        ]
        latest_on_page = max(page_dates).isoformat() if page_dates else None
        state = replace(
            state,
            pending_latest_release_date=self._max_iso_date(
                state.pending_latest_release_date,
                latest_on_page,
            ),
        )
        self.state_store.save(actor.actor_id, state)

        numbers: list[str] = []
        reached_cutoff = False
        if full:
            numbers = [
                movie.movie_number
                for movie in page_data.movies
                if movie.movie_number
            ]
        else:
            cutoff = self._incremental_cutoff(state.latest_release_date)
            for movie in page_data.movies:
                if not movie.movie_number:
                    continue
                release_date = movie.release_date
                if (
                    cutoff is not None
                    and release_date is not None
                    and release_date < cutoff
                ):
                    reached_cutoff = True
                    continue
                numbers.append(movie.movie_number)

        next_cursor = self._next_cursor(
            star_index=star_index,
            page=page,
            has_next=page_data.has_next,
            reached_cutoff=reached_cutoff,
            star_count=len(star_ids),
        )
        if next_cursor is None:
            state = replace(
                state,
                full_synced_at=(
                    self._clock() if full else state.full_synced_at
                ),
                last_synced_at=self._clock(),
                latest_release_date=self._max_iso_date(
                    state.latest_release_date,
                    state.pending_latest_release_date,
                ),
                pending_latest_release_date=None,
            )
            self.state_store.save(actor.actor_id, state)
        logger.info(
            "JavBus actor movies page done actor_id={} actor_name={} "
            "page={} phase={} star_index={} emitted={} latest_release_date={} "
            "reached_cutoff={} next_cursor={}",
            actor.actor_id,
            actor.name,
            page,
            "full" if full else "incremental",
            star_index,
            len(numbers),
            latest_on_page,
            reached_cutoff,
            next_cursor,
        )
        return PluginActorMoviePage(
            movie_numbers=numbers,
            next_cursor=next_cursor,
        )

    def _load_state(
        self,
        actor: PluginActorRef,
        fingerprint: str,
    ) -> ActorSyncState:
        state = self.state_store.get(actor.actor_id)
        if state is None:
            return ActorSyncState(fingerprint=fingerprint)
        if state.fingerprint == fingerprint:
            return state
        logger.info(
            "JavBus actor movies state reset actor_id={} actor_name={} "
            "reason=identity_changed",
            actor.actor_id,
            actor.name,
        )
        return ActorSyncState(fingerprint=fingerprint)

    def _fetch_available_page(
        self,
        *,
        actor: PluginActorRef,
        star_ids: tuple[str, ...],
        star_index: int,
        page: int,
        mode: str,
    ) -> tuple[ParsedActorMoviePage | None, int, int]:
        """返回第一个可用页面；单个 star 首页不可用时跳过该 star。"""
        while star_index < len(star_ids):
            star_id = star_ids[star_index]
            logger.info(
                "JavBus actor movies request actor_id={} actor_name={} "
                "page={} mode={} star_id={}",
                actor.actor_id,
                actor.name,
                page,
                mode,
                star_id,
            )
            page_data = self.source.get_actor_movies(
                star_id=star_id,
                page=page,
            )
            if page_data is not None:
                return page_data, star_index, page
            if page > 1:
                logger.warning(
                    "JavBus actor movies page unavailable actor_id={} "
                    "actor_name={} page={} star_id={}",
                    actor.actor_id,
                    actor.name,
                    page,
                    star_id,
                )
                raise JavBusPageError(
                    self.source.i18n.t(
                        "errors.actor_movies_unavailable",
                        default="JavBus 演员影片页不可用",
                    )
                )
            logger.warning(
                "JavBus actor movies star page unavailable, skipping "
                "actor_id={} actor_name={} star_id={}",
                actor.actor_id,
                actor.name,
                star_id,
            )
            star_index += 1
            page = 1
        return None, star_index, page

    def _ensure_star_ids(
        self,
        actor: PluginActorRef,
        state: ActorSyncState,
    ) -> ActorSyncState | None:
        if state.source_actor_ids:
            return state
        logger.info(
            "JavBus actor movies resolve source actors actor_id={} "
            "actor_name={} aliases={}",
            actor.actor_id,
            actor.name,
            actor.alias_names,
        )
        star_ids = self.source.find_actors(
            name=actor.name,
            alias_names=actor.alias_names,
        )
        if not star_ids:
            logger.warning(
                "JavBus actor movies unresolved actor_id={} actor_name={} "
                "aliases={}",
                actor.actor_id,
                actor.name,
                actor.alias_names,
            )
            self.state_store.save(actor.actor_id, state)
            return None
        logger.info(
            "JavBus actor movies resolved actor_id={} actor_name={} "
            "star_ids={}",
            actor.actor_id,
            actor.name,
            list(star_ids),
        )
        state = replace(state, source_actor_ids=tuple(star_ids))
        self.state_store.save(actor.actor_id, state)
        return state

    @staticmethod
    def _cursor_position(
        cursor: str | None,
        star_count: int,
    ) -> tuple[int, int]:
        """游标形如 ``<star 下标>:<页码>``。"""
        if cursor is None:
            return 0, 1
        try:
            index_text, page_text = cursor.split(":")
            star_index = int(index_text)
            page = int(page_text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"无效的演员影片游标: {cursor}") from exc
        if star_index < 0 or star_index >= star_count or page < 1:
            raise ValueError(f"无效的演员影片游标: {cursor}")
        return star_index, page

    @staticmethod
    def _next_cursor(
        *,
        star_index: int,
        page: int,
        has_next: bool,
        reached_cutoff: bool,
        star_count: int,
    ) -> str | None:
        if has_next and not reached_cutoff:
            return f"{star_index}:{page + 1}"
        if star_index + 1 < star_count:
            return f"{star_index + 1}:1"
        return None

    @staticmethod
    def _incremental_cutoff(latest_release_date: str | None) -> date | None:
        latest = parse_date(latest_release_date)
        if latest is None:
            return None
        return latest - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)

    @staticmethod
    def _max_iso_date(*values: str | date | None) -> str | None:
        dates = [parsed for value in values if (parsed := parse_date(value))]
        return max(dates).isoformat() if dates else None
