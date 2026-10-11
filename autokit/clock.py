"""Time source abstraction so automations can run against a simulated clock."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic seconds."""

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds`` (simulated clocks advance the simulation)."""


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)
