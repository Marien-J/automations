"""Virtual clock + simulated desktop.

A :class:`SimWorld` renders the screen and reacts to input. :class:`SimDesktop`
adapts it to the framework's ``ScreenSource`` and ``InputBackend`` interfaces,
and records every input event so tests can assert e.g. that nothing was sent
after an emergency stop. :class:`SimClock` advances the world whenever the
automation sleeps, so runs are fast and fully deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from autokit.geometry import Rect
from autokit.vision.frame import Frame


class SimWorld(Protocol):
    width: int
    height: int
    cursor: tuple[int, int]
    version: int  # must change whenever the rendered image would change

    def advance_to(self, t: float) -> None: ...

    def render(self) -> np.ndarray: ...

    def on_move(self, x: int, y: int) -> None: ...

    def on_button(self, button: str, down: bool) -> None: ...

    def on_key(self, key: str, down: bool) -> None: ...

    def report(self) -> dict[str, Any]:
        """Ground-truth statistics for the simulation summary."""


@dataclass
class SimulationBundle:
    """What an automation's ``simulation()`` hook returns."""

    world: SimWorld
    profile: Any  # autokit.runtime.profile.Profile, pre-calibrated for the world


class SimClock:
    def __init__(self) -> None:
        self._t = 0.0
        self._world: SimWorld | None = None

    def attach(self, world: SimWorld) -> None:
        self._world = world

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._t += seconds
        if self._world is not None:
            self._world.advance_to(self._t)


@dataclass(frozen=True)
class InputEvent:
    t: float
    kind: str  # "move" | "button" | "key"
    detail: tuple


class SimDesktop:
    def __init__(self, world: SimWorld, clock: SimClock) -> None:
        self.world = world
        self.clock = clock
        clock.attach(world)
        self.events: list[InputEvent] = []
        self._cache_version: int | None = None
        self._cache: np.ndarray | None = None
        self.grabs = 0

    # -- ScreenSource --------------------------------------------------------
    @property
    def bounds(self) -> Rect:
        return Rect(0, 0, self.world.width, self.world.height)

    def grab(self, region: Rect | None = None) -> Frame:
        self.grabs += 1
        if self._cache is None or self._cache_version != self.world.version:
            self._cache = self.world.render()
            self._cache_version = self.world.version
        frame = Frame(self._cache, 0, 0, self.clock.now()).crop(region)
        if frame is None:
            raise ValueError(f"region {region} outside the simulated screen")
        return frame

    # -- InputBackend --------------------------------------------------------
    def position(self) -> tuple[int, int]:
        return self.world.cursor

    def move(self, x: int, y: int) -> None:
        self.events.append(InputEvent(self.clock.now(), "move", (x, y)))
        self.world.on_move(x, y)

    def button(self, button: str, down: bool) -> None:
        self.events.append(InputEvent(self.clock.now(), "button", (button, down)))
        self.world.on_button(button, down)

    def key(self, key: str, down: bool) -> None:
        self.events.append(InputEvent(self.clock.now(), "key", (key, down)))
        self.world.on_key(key, down)

    def inputs_after(self, t: float) -> list[InputEvent]:
        """Input events sent after time ``t`` (ignoring releases of held inputs)."""
        return [e for e in self.events if e.t > t and not (e.kind in ("button", "key") and e.detail[1] is False)]
