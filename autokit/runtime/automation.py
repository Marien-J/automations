"""Automation base class and the per-run context handed to it."""

from __future__ import annotations

import dataclasses
import random
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import numpy as np

from autokit.clock import Clock
from autokit.errors import RecoverableError, UnsafeState, WaitTimeout
from autokit.events import RunLog
from autokit.geometry import Point, Rect
from autokit.input.controller import InputController
from autokit.runtime.profile import Profile
from autokit.safety import EmergencyStop, interruptible_sleep
from autokit.vision.capture import ScreenSource
from autokit.vision.frame import Frame

T = TypeVar("T")


@dataclass(frozen=True)
class AssetSpec:
    """Something the user must calibrate for an automation.

    ``kind`` is ``"template"`` (a reference image), ``"color"`` (an RGB colour
    with tolerance) or ``"region"`` (a screen rectangle). ``instructions`` are
    shown by the calibration wizard. ``default`` (colours only) is used when the
    user does not calibrate an optional colour. ``isolated`` (templates only)
    marks whole-word labels that must not match inside longer text.
    """

    name: str
    kind: str
    instructions: str
    required: bool = True
    default: Any = None
    group: str = "core"
    isolated: bool = False


@dataclass
class RunSummary:
    stop_reason: str
    completed: bool  # True when the run reached its goal, False when it stopped early
    stats: dict[str, Any] = field(default_factory=dict)


class Context:
    """Everything an automation needs to observe the screen and act on it."""

    def __init__(
        self,
        *,
        clock: Clock,
        stop: EmergencyStop,
        screen: ScreenSource,
        input: InputController,
        profile: Profile,
        log: RunLog,
        rng: random.Random,
        poll_interval: float = 0.08,
    ) -> None:
        self.clock = clock
        self.stop = stop
        self.screen = screen
        self.input = input
        self.profile = profile
        self.log = log
        self.rng = rng
        self.poll_interval = poll_interval

    def now(self) -> float:
        return self.clock.now()

    def grab(self, region: Rect | None = None) -> Frame:
        self.stop.check()
        frame = self.screen.grab(region)
        return Frame(frame.image, frame.ox, frame.oy, self.clock.now())

    def sleep(self, seconds: float) -> None:
        interruptible_sleep(self.clock, self.stop, seconds)

    def pause(self, low: float, high: float) -> None:
        self.sleep(self.rng.uniform(low, high))

    def wait_until(
        self,
        predicate: Callable[[Frame], T | None],
        timeout: float,
        desc: str,
        *,
        region: Rect | None = None,
    ) -> T:
        """Poll the screen until ``predicate`` returns a truthy value.

        The timeout is a safeguard only: success is always a confirmed visual
        state, never the mere passage of time.
        """
        deadline = self.clock.now() + timeout
        while True:
            result = predicate(self.grab(region))
            if result:
                return result
            if self.clock.now() >= deadline:
                raise WaitTimeout(f"timed out after {timeout:.1f}s waiting for {desc}")
            self.sleep(self.poll_interval * self.rng.uniform(0.8, 1.2))


class Automation(ABC):
    """Base class for automations.

    Subclasses declare their metadata as class attributes, parse their settings
    in ``__init__`` and implement :meth:`step`. The default :meth:`run` loop
    calls :meth:`step` until :meth:`finished` returns a reason, and converts
    repeated recoverable failures into an :class:`UnsafeState` stop.
    """

    id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    assets: ClassVar[tuple[AssetSpec, ...]] = ()
    anchor_template: ClassVar[str | None] = None
    package_dir: ClassVar[Path]

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.max_consecutive_failures = 3
        self._failures: list[str] = []
        self._apply_template_options()

    def _apply_template_options(self) -> None:
        library = self.ctx.profile.templates
        overrides = self.ctx.profile.calibration.get("templates", {})
        for spec in self.assets:
            if spec.kind == "template" and spec.isolated and library.has(spec.name):
                if "isolated" not in overrides.get(spec.name, {}):
                    library.add(dataclasses.replace(library.get(spec.name), isolated=True))

    # -- metadata ------------------------------------------------------------
    @classmethod
    def defaults_file(cls) -> Path:
        return cls.package_dir / "defaults.yaml"

    @classmethod
    def simulation(cls, scenario: str, seed: int) -> Any:
        """Return a simulation bundle for dry runs, or ``None`` if unsupported."""
        return None

    @classmethod
    def required_groups(cls, settings: Mapping[str, Any]) -> set[str] | None:
        """Asset groups the given settings need (``None`` = every group)."""
        return None

    @classmethod
    def missing_assets(cls, profile: Profile, groups: set[str] | None = None) -> list[AssetSpec]:
        """Required assets (in ``groups``, default all) that are not calibrated."""
        missing = []
        for spec in cls.assets:
            if not spec.required or (groups is not None and spec.group not in groups):
                continue
            if spec.kind == "template" and not profile.templates.has(spec.name):
                missing.append(spec)
            elif spec.kind == "color" and profile.color(spec.name) is None and spec.default is None:
                missing.append(spec)
            elif spec.kind == "region" and profile.region(spec.name) is None:
                missing.append(spec)
        return missing

    # -- lifecycle -----------------------------------------------------------
    def validate(self) -> list[str]:
        """Problems that prevent a safe start (empty list when ready)."""
        return [f"{s.kind} '{s.name}' is not calibrated" for s in self.missing_assets(self.ctx.profile)]

    def finished(self) -> str | None:
        """A reason string once the run's goal is reached."""
        return None

    @abstractmethod
    def step(self) -> None:
        """Observe the screen once and perform at most one verified action."""

    def stats(self) -> dict[str, Any]:
        return {}

    def diagnose(self, frame: Frame) -> tuple[list[str], np.ndarray | None]:
        """Describe what the automation recognises in ``frame`` (no input sent)."""
        return [], None

    def hover_targets(self, frame: Frame) -> list[tuple[str, Point]]:
        """Points ``check --hover`` moves the mouse to, to verify click alignment."""
        return []

    def mark_progress(self) -> None:
        """Call after a verified successful action; resets the failure budget."""
        self._failures.clear()

    def record_failure(self, reason: str) -> None:
        self._failures.append(reason)
        self.ctx.log.event("recoverable_failure", reason=reason, consecutive=len(self._failures))
        if len(self._failures) >= self.max_consecutive_failures:
            raise UnsafeState(f"giving up after {len(self._failures)} consecutive failed interactions; last: {reason}")

    def run(self) -> RunSummary:
        while True:
            reason = self.finished()
            if reason:
                return RunSummary(reason, True, self.stats())
            try:
                self.step()
            except RecoverableError as exc:
                self.record_failure(str(exc))
