"""Guarded, human-paced input.

Every mouse step, button event and key event first checks the emergency stop,
and every mouse step also checks that the cursor is still where the automation
left it. If the user grabs the mouse the run stops immediately.
"""

from __future__ import annotations

import logging
import math
import random

from autokit.clock import Clock
from autokit.errors import EmergencyStopped
from autokit.geometry import Point
from autokit.input.backend import InputBackend
from autokit.input.motion import MotionProfile, plan_path
from autokit.safety import EmergencyStop, interruptible_sleep

log = logging.getLogger(__name__)


class InputController:
    def __init__(
        self,
        backend: InputBackend,
        clock: Clock,
        stop: EmergencyStop,
        rng: random.Random,
        profile: MotionProfile | None = None,
        override_tolerance_px: float = 30.0,
    ) -> None:
        self.backend = backend
        self.clock = clock
        self.stop = stop
        self.rng = rng
        self.profile = profile or MotionProfile()
        self.override_tolerance_px = override_tolerance_px
        self._last: tuple[int, int] | None = None
        self._held_buttons: set[str] = set()
        self._held_keys: set[str] = set()

    # -- helpers -------------------------------------------------------------
    def _sleep(self, seconds: float) -> None:
        interruptible_sleep(self.clock, self.stop, seconds)

    def _pause(self, bounds: tuple[float, float]) -> None:
        self._sleep(self.rng.uniform(*bounds))

    def _check_override(self) -> None:
        if self._last is None or self.override_tolerance_px <= 0:
            return
        x, y = self.backend.position()
        if math.hypot(x - self._last[0], y - self._last[1]) > self.override_tolerance_px:
            self.stop.trigger(f"manual mouse movement detected (cursor at {x},{y}, expected {self._last})")
            raise EmergencyStopped(self.stop.reason or "manual mouse movement")

    def _guard(self) -> None:
        self.stop.check()
        self._check_override()

    # -- mouse ---------------------------------------------------------------
    @property
    def position(self) -> Point:
        x, y = self.backend.position()
        return Point(x, y)

    def move_to(self, target: Point) -> None:
        self._guard()
        x, y = self.backend.position()
        for px, py, dt in plan_path(Point(x, y), target, self.profile, self.rng):
            self._guard()
            self.backend.move(px, py)
            self._last = (px, py)
            self._sleep(dt)

    def click(self, target: Point | None = None, button: str = "left") -> None:
        """Move to ``target`` (if given) and click there."""
        if target is not None:
            self.move_to(target)
        self._pause(self.profile.pre_click_s)
        self._guard()
        self._press_button(button)
        self._pause(self.profile.post_click_s)

    def right_click(self, target: Point | None = None) -> None:
        self.click(target, "right")

    def _press_button(self, button: str) -> None:
        self.backend.button(button, True)
        self._held_buttons.add(button)
        try:
            self._pause(self.profile.click_hold_s)
        finally:
            self.backend.button(button, False)
            self._held_buttons.discard(button)

    # -- keyboard ------------------------------------------------------------
    def press_key(self, key: str) -> None:
        self._guard()
        self.backend.key(key, True)
        self._held_keys.add(key)
        try:
            self._pause(self.profile.key_hold_s)
        finally:
            self.backend.key(key, False)
            self._held_keys.discard(key)
        self._pause(self.profile.between_keys_s)

    def type_text(self, text: str) -> None:
        for ch in text:
            self.press_key(ch)

    # -- shutdown ------------------------------------------------------------
    def release_all(self) -> None:
        """Release anything still held (used when a run ends abnormally)."""
        for button in list(self._held_buttons):
            try:
                self.backend.button(button, False)
            except Exception:  # pragma: no cover - best effort
                log.exception("failed to release %s button", button)
        for key in list(self._held_keys):
            try:
                self.backend.key(key, False)
            except Exception:  # pragma: no cover - best effort
                log.exception("failed to release key %s", key)
        self._held_buttons.clear()
        self._held_keys.clear()
