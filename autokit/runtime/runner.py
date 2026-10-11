"""Runs an automation with the safety net around it.

Whatever happens — goal reached, emergency stop, unsafe state, crash — the
runner latches the emergency stop (so no further input can be sent), releases
held buttons/keys, saves a screenshot when the stop was caused by the screen,
and records the reason.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autokit.clock import Clock, RealClock
from autokit.errors import ConfigError, EmergencyStopped, RecoverableError, UnsafeState
from autokit.events import RunLog
from autokit.input.backend import InputBackend
from autokit.input.controller import InputController
from autokit.input.motion import MotionProfile
from autokit.runtime.automation import Automation, Context, RunSummary
from autokit.runtime.profile import Profile
from autokit.safety import EmergencyStop, HotkeyWatcher, StopFileWatcher
from autokit.vision.capture import ScreenSource

log = logging.getLogger(__name__)


@dataclass
class RunEnvironment:
    """The pieces a run needs; real or simulated."""

    clock: Clock
    screen: ScreenSource
    backend: InputBackend
    stop: EmergencyStop = field(default_factory=EmergencyStop)
    log_dir: Path | None = None
    seed: int | None = None
    cleanups: list[Callable[[], None]] = field(default_factory=list)


def build_context(profile: Profile, env: RunEnvironment) -> Context:
    safety = profile.settings.get("safety", {})
    timing = profile.settings.get("timing", {})
    rng = random.Random(env.seed)
    controller = InputController(
        env.backend,
        env.clock,
        env.stop,
        rng,
        MotionProfile.from_dict(profile.settings.get("mouse", {})),
        override_tolerance_px=float(safety.get("manual_mouse_tolerance_px", 30)),
    )
    return Context(
        clock=env.clock,
        stop=env.stop,
        screen=env.screen,
        input=controller,
        profile=profile,
        log=RunLog(env.clock, env.log_dir),
        rng=rng,
        poll_interval=float(timing.get("poll_interval_s", 0.08)),
    )


def live_environment(profile: Profile, monitor: int = 1) -> RunEnvironment:
    """Real screen, real mouse/keyboard, emergency hotkey and stop file."""
    from autokit.input.backend import PynputBackend
    from autokit.vision.capture import MssScreen

    safety = profile.settings.get("safety", {})
    stop = EmergencyStop()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    env = RunEnvironment(RealClock(), MssScreen(monitor), PynputBackend(), stop, profile.runs_dir / stamp)
    stop_file = profile.root / str(safety.get("stop_file", "STOP"))
    if stop_file.exists():
        log.info("removing stale stop file %s", stop_file)
        stop_file.unlink()
    file_watcher = StopFileWatcher(stop, stop_file)
    file_watcher.start()
    env.cleanups.append(file_watcher.close)
    hotkey = HotkeyWatcher(stop, str(safety.get("emergency_hotkey", "<f12>")))
    if hotkey.start():
        env.cleanups.append(hotkey.close)
        log.info("emergency stop: press %s, move the mouse, or create %s", hotkey.hotkey, stop_file)
    else:
        log.info("emergency stop: move the mouse, press Ctrl+C here, or create %s", stop_file)
    return env


def execute(automation: Automation) -> RunSummary:
    ctx = automation.ctx
    summary: RunSummary
    try:
        problems = automation.validate()
        if problems:
            raise ConfigError("profile is not ready:\n  - " + "\n  - ".join(problems))
        ctx.log.event("run_started", automation=automation.id)
        summary = automation.run()
    except EmergencyStopped as exc:
        summary = RunSummary(f"emergency stop: {exc}", False, automation.stats())
    except (UnsafeState, RecoverableError) as exc:
        ctx.stop.trigger(f"unsafe state: {exc}")
        _save_screen(ctx, "unsafe_state")
        summary = RunSummary(f"stopped safely: {exc}", False, automation.stats())
    except ConfigError as exc:
        summary = RunSummary(f"configuration error: {exc}", False, automation.stats())
    except KeyboardInterrupt:
        ctx.stop.trigger("Ctrl+C")
        summary = RunSummary("interrupted with Ctrl+C", False, automation.stats())
    except Exception as exc:
        ctx.stop.trigger(f"crash: {exc!r}")
        log.exception("automation crashed")
        _save_screen(ctx, "crash")
        summary = RunSummary(f"crashed: {exc!r}", False, automation.stats())
    finally:
        ctx.stop.trigger("run finished", announce=False)  # latch: no input after this point
        ctx.input.release_all()
    ctx.log.event("run_finished", reason=summary.stop_reason, completed=summary.completed, **summary.stats)
    ctx.log.close()
    return summary


def _save_screen(ctx: Context, label: str) -> None:
    try:
        path = ctx.log.save_frame(ctx.screen.grab(), label)
        if path:
            ctx.log.info("saved screenshot of the stop state: %s", path)
    except Exception:  # pragma: no cover - diagnostics are best effort
        log.exception("could not save screenshot")


def run_with_environment(automation_cls: type[Automation], profile: Profile, env: RunEnvironment) -> RunSummary:
    try:
        ctx = build_context(profile, env)
        automation = automation_cls(ctx)
        return execute(automation)
    finally:
        for cleanup in env.cleanups:
            cleanup()
