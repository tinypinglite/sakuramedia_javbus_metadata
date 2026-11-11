from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from src.plugins import PluginActorMovieQuery, PluginActorRef

from sakuramedia_javbus_metadata.actor_sync import (
    JavbusActorMovieSource,
    JavbusActorStateStore,
)
from sakuramedia_javbus_metadata.javbus import (
    JavBusPageError,
    ParsedActorMovie,
    ParsedActorMoviePage,
)

from .helpers import FakeClock, FakeI18n


class FakeSource:
    def __init__(self, pages, *, star_id: str | None = "2xi"):
        self.pages = pages
        self.star_id = star_id
        self.find_calls = 0
        self.i18n = FakeI18n()

    def find_actor(self, *, name: str, alias_names: list[str]):
        self.find_calls += 1
        return self.star_id

    def get_actor_movies(self, *, star_id: str, page: int):
        result = self.pages.get(page)
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
    clock: FakeClock | None = None,
):
    fake_clock = clock or FakeClock()
    fake_source = FakeSource(pages)
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
    assert first.next_cursor == "2"
    assert second.movie_numbers == ["ABC-003"]
    assert second.next_cursor is None
    state = store.get(1)
    assert state is not None
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
    source.fetch_actor_movies(_query(cursor="2"))

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
    assert first.next_cursor == "2"

    with pytest.raises(JavBusPageError):
        source.fetch_actor_movies(_query(cursor="2"))

    state = store.get(1)
    assert state is not None
    assert state.full_synced_at is None


def test_unresolved_actor_returns_none_and_keeps_retryable_state(
    tmp_path: Path,
) -> None:
    fake_source = FakeSource({}, star_id=None)
    store = JavbusActorStateStore(tmp_path / "actors.json")
    source = JavbusActorMovieSource(
        source=fake_source,
        state_store=store,
        clock=FakeClock(),
    )

    assert source.fetch_actor_movies(_query()) is None
    state = store.get(1)
    assert state is not None
    assert state.source_actor_id is None
    assert state.full_synced_at is None


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
