from __future__ import annotations

import sys

from sakuramedia_javbus_metadata.rate_limit import RateLimiter, is_aps_process

from .helpers import FakeClock


def test_rate_limiter_spaces_requests() -> None:
    clock = FakeClock(now=0)
    limiter = RateLimiter(3, clock=clock, sleep=clock.sleep)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert clock.sleeps == [3, 3]


def test_is_aps_process_by_argv(monkeypatch) -> None:
    monkeypatch.delenv("SAKURAMEDIA_JAVBUS_RATE_LIMIT", raising=False)
    monkeypatch.setattr(sys, "argv", ["python", "-m", "src.start.commands", "aps"])
    assert is_aps_process() is True
    monkeypatch.setattr(sys, "argv", ["uvicorn", "src.api.app:app"])
    assert is_aps_process() is False


def test_rate_limit_env_override(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["uvicorn", "src.api.app:app"])
    monkeypatch.setenv("SAKURAMEDIA_JAVBUS_RATE_LIMIT", "1")
    assert is_aps_process() is True
    monkeypatch.setenv("SAKURAMEDIA_JAVBUS_RATE_LIMIT", "0")
    assert is_aps_process() is False
