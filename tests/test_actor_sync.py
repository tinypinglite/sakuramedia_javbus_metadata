from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from src.plugins import PluginActorMovieQuery, PluginActorRef

from sakuramedia_javbus_metadata.actor_sync import (
    ActorSyncState,
    JavbusActorMovieSource,
    JavbusActorStateStore,
    actor_fingerprint,
)
from sakuramedia_javbus_metadata.javbus import (
    JavBusPageError,
    ParsedActorMovie,
    ParsedActorMoviePage,
)

from .helpers import FakeClock, FakeI18n


class FakeSource:
    def __init__(
        self,
        pages,
        *,
        star_ids: tuple[str, ...] = ("2xi",),
    ):
        self.pages = pages
        self.star_ids = star_ids
        self.find_calls = 0
        self.requests: list[tuple[str, int]] = []
        self.i18n = FakeI18n()

    def find_actors(self, *, name: str, alias_names: list[str]):
        self.find_calls += 1
        return self.star_ids

    def get_actor_movies(self, *, star_id: str, page: int):
        self.requests.append((star_id, page))
        result = self.pages.get((star_id, page), self.pages.get(page))
        if isinstance(result, Exception):
            raise result
        return result


def _page(
    movies: list[tuple[str, str | None]],
    *,
    has_next: bool,
) -> ParsedActorMoviePage:
    return ParsedActorMoviePage(
        movies=tuple(
            ParsedActorMovie(
                movie_number=number,
                release_date=(
                    date.fromisoformat(release_date)
                    if release_date
                    else None
                ),
            )
            for number, release_date in movies
        ),
        has_next=has_next,
    )


def _actor() -> PluginActorRef:
    return PluginActorRef(
        actor_id=1,
        name="葵つかさ",
        alias_names=["葵司"],
        gender=1,
    )


def _query(mode: str = "incremental", cursor: str | None = None):
    return PluginActorMovieQuery(actor=_actor(), cursor=cursor, mode=mode)


def _source(
    tmp_path: Path,
    pages,
    *,
    star_ids: tuple[str, ...] = ("2xi",),
    clock: FakeClock | None = None,
):
    fake_clock = clock or FakeClock()
    fake_source = FakeSource(pages, star_ids=star_ids)
    store = JavbusActorStateStore(tmp_path / "actors.json")
    source = JavbusActorMovieSource(
        source=fake_source,
        state_store=store,
        clock=fake_clock,
    )
    return source, fake_source, store


def test_first_incremental_request_is_full_scan(tmp_path: Path) -> None:
    source, fake_source, store = _source(
        tmp_path,
        {
            1: _page(
                [("ABC-001", "2024-02-01"), ("ABC-002", "2024-01-15")],
                has_next=True,
            ),
            2: _page([("ABC-003", "2023-12-01")], has_next=False),
        },
    )

    first = source.fetch_actor_movies(_query())
    second = source.fetch_actor_movies(_query(cursor=first.next_cursor))

    assert first.movie_numbers == ["ABC-001", "ABC-002"]
    assert first.next_cursor == "0:2"
    assert second.movie_numbers == ["ABC-003"]
    assert second.next_cursor is None
    state = store.get(1)
    assert state is not None
    assert state.source_actor_ids == ("2xi",)
    assert state.full_synced_at is not None
    assert state.latest_release_date == "2024-02-01"
    assert fake_source.find_calls == 1


def test_incremental_uses_lookback_and_stops_at_cutoff(tmp_path: Path) -> None:
    source, _fake_source, store = _source(
        tmp_path,
        {
            1: _page(
                [("ABC-001", "2024-02-01"), ("ABC-002", "2024-01-15")],
                has_next=True,
            ),
            2: _page([("ABC-003", "2023-12-01")], has_next=False),
        },
    )
    source.fetch_actor_movies(_query())
    source.fetch_actor_movies(_query(cursor="0:2"))

    source2, _fake_source2, _store2 = _source(
        tmp_path,
        {
            1: _page(
                [("ABC-004", "2024-03-01"), ("ABC-005", "2024-01-01")],
                has_next=True,
            ),
        },
    )
    result = source2.fetch_actor_movies(_query())

    assert result.movie_numbers == ["ABC-004"]
    assert result.next_cursor is None
    state = store.get(1)
    assert state is not None
    assert state.latest_release_date == "2024-03-01"


def test_full_mode_forces_full_scan_even_with_state(tmp_path: Path) -> None:
    source, fake_source, store = _source(
        tmp_path,
        {1: _page([("ABC-001", "2024-02-01")], has_next=False)},
    )
    source.fetch_actor_movies(_query())
    assert store.get(1).full_synced_at is not None  # type: ignore[union-attr]

    result = source.fetch_actor_movies(_query(mode="full"))

    assert result.movie_numbers == ["ABC-001"]
    assert result.next_cursor is None
    assert store.get(1).full_synced_at is not None  # type: ignore[union-attr]
    assert fake_source.find_calls == 1


def test_full_scan_failure_does_not_mark_full_synced(tmp_path: Path) -> None:
    source, _fake_source, store = _source(
        tmp_path,
        {
            1: _page(
                [("ABC-001", "2024-02-01"), ("ABC-002", "2024-01-15")],
                has_next=True,
            ),
            2: JavBusPageError("source down"),
        },
    )
    first = source.fetch_actor_movies(_query())
    assert first.next_cursor == "0:2"

    with pytest.raises(JavBusPageError):
        source.fetch_actor_movies(_query(cursor="0:2"))

    state = store.get(1)
    assert state is not None
    assert state.full_synced_at is None


def test_unresolved_actor_returns_none_and_keeps_retryable_state(
    tmp_path: Path,
) -> None:
    fake_source = FakeSource({}, star_ids=())
    store = JavbusActorStateStore(tmp_path / "actors.json")
    source = JavbusActorMovieSource(
        source=fake_source,
        state_store=store,
        clock=FakeClock(),
    )

    assert source.fetch_actor_movies(_query()) is None
    state = store.get(1)
    assert state is not None
    assert state.source_actor_ids == ()
    assert state.full_synced_at is None


def test_multiple_source_pages_are_merged(tmp_path: Path) -> None:
    source, fake_source, store = _source(
        tmp_path,
        {
            ("aa1", 1): _page([("ABC-001", "2024-02-01")], has_next=True),
            ("aa1", 2): _page([("ABC-002", "2024-01-15")], has_next=False),
            ("bb2", 1): _page([("ABC-003", "2023-12-01")], has_next=False),
        },
        star_ids=("aa1", "bb2"),
    )

    first = source.fetch_actor_movies(_query())
    second = source.fetch_actor_movies(_query(cursor=first.next_cursor))
    third = source.fetch_actor_movies(_query(cursor=second.next_cursor))

    assert first.movie_numbers == ["ABC-001"]
    assert first.next_cursor == "0:2"
    assert second.movie_numbers == ["ABC-002"]
    assert second.next_cursor == "1:1"
    assert third.movie_numbers == ["ABC-003"]
    assert third.next_cursor is None
    state = store.get(1)
    assert state is not None
    assert state.source_actor_ids == ("aa1", "bb2")
    assert state.full_synced_at is not None
    assert fake_source.find_calls == 1


def test_unavailable_star_page_is_skipped(tmp_path: Path) -> None:
    source, _fake_source, _store = _source(
        tmp_path,
        {
            ("aa1", 1): _page([("ABC-001", "2024-02-01")], has_next=False),
            ("bb2", 1): None,
            ("cc3", 1): _page([("ABC-003", "2023-12-01")], has_next=False),
        },
        star_ids=("aa1", "bb2", "cc3"),
    )

    first = source.fetch_actor_movies(_query())
    second = source.fetch_actor_movies(_query(cursor="1:1"))

    assert first.movie_numbers == ["ABC-001"]
    assert first.next_cursor == "1:1"
    assert second.movie_numbers == ["ABC-003"]
    assert second.next_cursor is None


def test_v1_state_is_migrated_to_source_actor_ids(tmp_path: Path) -> None:
    path = tmp_path / "actors.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "actors": {
                    "1": {
                        "fingerprint": actor_fingerprint(_actor()),
                        "source_actor_id": "2xi",
                        "full_synced_at": 1.5,
                        "last_synced_at": 2.5,
                        "latest_release_date": "2024-02-01",
                        "pending_latest_release_date": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = JavbusActorStateStore(path).get(1)

    assert state is not None
    assert state.source_actor_ids == ("2xi",)
    assert state.full_synced_at == 1.5
    assert state.latest_release_date == "2024-02-01"


def test_actor_movie_404_re_resolves_actor_once(tmp_path: Path) -> None:
    class RetrySource(FakeSource):
        def __init__(self):
            super().__init__({})
            self.retried = False

        def get_actor_movies(self, *, star_id: str, page: int):
            if not self.retried:
                self.retried = True
                return None
            return _page([("ABC-010", "2024-01-01")], has_next=False)

    fake_source = RetrySource()
    store = JavbusActorStateStore(tmp_path / "actors.json")
    source = JavbusActorMovieSource(
        source=fake_source,
        state_store=store,
        clock=FakeClock(),
    )

    result = source.fetch_actor_movies(_query())

    assert result is not None
    assert result.movie_numbers == ["ABC-010"]
    assert fake_source.find_calls == 2


def test_re_resolve_after_404_keeps_full_sync_marker(tmp_path: Path) -> None:
    class RetrySource(FakeSource):
        def __init__(self):
            super().__init__(
                {
                    ("2xi", 1): _page([("ABC-010", "2024-01-01")], has_next=False),
                }
            )
            self.retried = False

        def get_actor_movies(self, *, star_id: str, page: int):
            if not self.retried:
                self.retried = True
                return None
            return super().get_actor_movies(star_id=star_id, page=page)

    store = JavbusActorStateStore(tmp_path / "actors.json")
    store.save(
        1,
        ActorSyncState(
            fingerprint=actor_fingerprint(_actor()),
            source_actor_ids=("2xi",),
            full_synced_at=1.0,
            last_synced_at=1.0,
            latest_release_date="2024-01-01",
        ),
    )
    source = JavbusActorMovieSource(
        source=RetrySource(),
        state_store=store,
        clock=FakeClock(),
    )

    result = source.fetch_actor_movies(_query())

    assert result is not None
    assert result.next_cursor is None
    state = store.get(1)
    assert state is not None
    assert state.full_synced_at is not None


def test_actor_name_change_resets_state_and_forces_full(tmp_path: Path) -> None:
    source, fake_source, store = _source(
        tmp_path,
        {1: _page([("ABC-001", "2024-02-01")], has_next=False)},
    )
    source.fetch_actor_movies(_query())

    changed_query = PluginActorMovieQuery(
        actor=PluginActorRef(
            actor_id=1,
            name="改名演员",
            alias_names=[],
            gender=1,
        ),
        cursor=None,
        mode="incremental",
    )
    result = source.fetch_actor_movies(changed_query)

    assert result is not None
    assert fake_source.find_calls == 2
    assert store.get(1) is not None
