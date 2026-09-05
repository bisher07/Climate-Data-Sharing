"""Injectable clocks.

Transaction timestamps must be reproducible in experiments (Section 14), so no
module calls `datetime.now()` directly; a clock is passed in instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "SystemClock", "FixedClock"]


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests and replayable experiment runs."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware start time")
        self._current = start
        self._step = step

    def now(self) -> datetime:
        value = self._current
        self._current += self._step
        return value
