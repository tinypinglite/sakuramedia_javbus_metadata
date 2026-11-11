"""APS 进程内的 JavBus 页面请求间隔限制。"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable


def is_aps_process() -> bool:
    override = os.environ.get("SAKURAMEDIA_JAVBUS_RATE_LIMIT")
    if override is not None:
        return override.strip().lower() not in {"0", "false", "no", "off"}
    args = [part.lower() for part in sys.argv]
    if "aps" in args:
        return True
    command = " ".join(args)
    return "src.start.commands aps" in command or command.endswith(" aps")


class RateLimiter:
    """线程安全的固定最小请求间隔。"""

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval_seconds = max(float(interval_seconds), 0.0)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        if self.interval_seconds <= 0:
            return
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._next_request_at = max(now, self._next_request_at) + self.interval_seconds
