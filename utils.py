"""无宿主依赖的小工具。"""

from __future__ import annotations

from datetime import date
from typing import Any


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None
