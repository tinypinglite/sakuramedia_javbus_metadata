from __future__ import annotations

from typing import Any


class FakeI18n:
    def t(self, key: str, default: str | None = None, **params: Any) -> str:
        return default if default is not None else key


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds
