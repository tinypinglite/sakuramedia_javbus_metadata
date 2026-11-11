"""订阅演员影片列表：插件自管首次全量与后续增量状态。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from src.plugins import (
    PluginActorMoviePage,
    PluginActorMovieQuery,
    PluginActorRef,
)

from .javbus import JavBusMetadataSource, JavBusPageError
from .state import exclusive_file_lock, read_json, write_json_atomic
from .utils import parse_date

STATE_VERSION = 1
INCREMENTAL_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ActorSyncState:
    fingerprint: str
    source_actor_id: str | None = None
    full_synced_at: float | None = None
    last_synced_at: float | None = None
    latest_release_date: str | None = None
    pending_latest_release_date: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ActorSyncState | None:
        if not isinstance(payload, dict):
            return None
        fingerprint = payload.get("fingerprint")
        source_actor_id = payload.get("source_actor_id")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        if source_actor_id is not None and not isinstance(source_actor_id, str):
            return None
        return cls(
            fingerprint=fingerprint,
            source_actor_id=source_actor_id or None,
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
            "source_actor_id": self.source_actor_id,
            "full_synced_at": self.full_synced_at,
            "last_synced_at": self.last_synced_at,
            "latest_release_date": self.latest_release_date,
            "pending_latest_release_date": self.pending_latest_release_date,
        }


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
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
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
        state = self.state_store.get(actor.actor_id)
        if state is None or state.fingerprint != fingerprint:
            state = ActorSyncState(fingerprint=fingerprint)

        page = self._page_number(query.cursor)
        if page == 1:
            state = replace(
                state,
                pending_latest_release_date=None,
                full_synced_at=(
                    None if query.mode == "full" else state.full_synced_at
                ),
            )
            self.state_store.save(actor.actor_id, state)

        state = self._ensure_star_id(actor, state)
        if state is None:
            return None

        page_data = self.source.get_actor_movies(
            star_id=state.source_actor_id,
            page=page,
        )
        if page_data is None:
            if page != 1:
                raise JavBusPageError(
                    self.source.i18n.t(
                        "errors.actor_movies_unavailable",
                        default="JavBus 演员影片页不可用",
                    )
                )
            state = ActorSyncState(fingerprint=fingerprint)
            state = self._ensure_star_id(actor, state)
            if state is None:
                return None
            page_data = self.source.get_actor_movies(
                star_id=state.source_actor_id,
                page=page,
            )
            if page_data is None:
                return None

        page_dates = [
            movie.release_date
            for movie in page_data.movies
            if movie.release_date is not None
        ]
        state = replace(
            state,
            pending_latest_release_date=self._max_iso_date(
                state.pending_latest_release_date,
                max(page_dates) if page_dates else None,
            ),
        )
        self.state_store.save(actor.actor_id, state)

        full = query.mode == "full" or state.full_synced_at is None
        if full:
            numbers = [
                movie.movie_number
                for movie in page_data.movies
                if movie.movie_number
            ]
            next_cursor = (
                str(page + 1) if page_data.has_next else None
            )
            if next_cursor is None:
                state = replace(
                    state,
                    full_synced_at=self._clock(),
                    last_synced_at=self._clock(),
                    latest_release_date=self._max_iso_date(
                        state.latest_release_date,
                        state.pending_latest_release_date,
                    ),
                    pending_latest_release_date=None,
                )
                self.state_store.save(actor.actor_id, state)
            return PluginActorMoviePage(
                movie_numbers=numbers,
                next_cursor=next_cursor,
            )

        cutoff = self._incremental_cutoff(state.latest_release_date)
        numbers = []
        reached_cutoff = False
        for movie in page_data.movies:
            if not movie.movie_number:
                continue
            release_date = movie.release_date
            if cutoff is not None and release_date is not None:
                if release_date < cutoff:
                    reached_cutoff = True
                    continue
            numbers.append(movie.movie_number)
        next_cursor = (
            str(page + 1)
            if page_data.has_next and not reached_cutoff
            else None
        )
        if next_cursor is None:
            state = replace(
                state,
                last_synced_at=self._clock(),
                latest_release_date=self._max_iso_date(
                    state.latest_release_date,
                    state.pending_latest_release_date,
                ),
                pending_latest_release_date=None,
            )
            self.state_store.save(actor.actor_id, state)
        return PluginActorMoviePage(
            movie_numbers=numbers,
            next_cursor=next_cursor,
        )

    def _ensure_star_id(
        self,
        actor: PluginActorRef,
        state: ActorSyncState,
    ) -> ActorSyncState | None:
        if state.source_actor_id:
            return state
        star_id = self.source.find_actor(
            name=actor.name,
            alias_names=actor.alias_names,
        )
        if star_id is None:
            self.state_store.save(actor.actor_id, state)
            return None
        state = replace(state, source_actor_id=star_id)
        self.state_store.save(actor.actor_id, state)
        return state

    @staticmethod
    def _page_number(cursor: str | None) -> int:
        if cursor is None:
            return 1
        try:
            page = int(cursor)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"无效的演员影片游标: {cursor}") from exc
        if page < 1:
            raise ValueError(f"无效的演员影片游标: {cursor}")
        return page

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
