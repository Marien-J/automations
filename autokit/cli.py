"""Command line interface: ``python -m autokit <command> ...``.

Commands
  list                         available automations
  init AUTOMATION              create a profile with an editable settings.yaml
  calibrate AUTOMATION         capture templates / colours / regions (wizard)
  set-color AUTOMATION NAME R,G,B   set a colour exactly (e.g. a RuneLite marker colour)
  check AUTOMATION             observe the screen once and report what is recognised (no clicks)
  run AUTOMATION               run unattended
  simulate AUTOMATION          dry run against the built-in simulator
  capture                      save a plain screenshot
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from autokit.errors import ConfigError
from autokit.runtime.automation import Automation
from autokit.runtime.profile import Profile
from autokit.runtime.registry import discover, resolve

REPO_ROOT = Path(__file__).resolve().parents[1]


def profiles_root() -> Path:
    return Path(os.environ.get("AUTOKIT_PROFILES", REPO_ROOT / "profiles"))


def profile_dir(automation: type[Automation], name: str) -> Path:
    return profiles_root() / automation.id / name


def load_profile(automation: type[Automation], name: str) -> Profile:
    root = profile_dir(automation, name)
    if not root.exists():
        raise ConfigError(f"profile '{name}' does not exist; create it with `python -m autokit init {automation.id}`")
    return Profile.load(root, automation.defaults_file())


def _print_summary(summary) -> None:
    print("\n=== run summary ===")
    print(f"result: {'completed' if summary.completed else 'stopped'} — {summary.stop_reason}")
    for key, value in summary.stats.items():
        print(f"  {key}: {value}")


def cmd_list(args: argparse.Namespace) -> int:
    for automation_id, cls in sorted(discover().items()):
        print(f"{automation_id}\n    {cls.title}: {cls.description}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    cls = resolve(args.automation)
    root = profile_dir(cls, args.profile)
    settings = root / "settings.yaml"
    if settings.exists():
        print(f"{settings} already exists; leaving it unchanged")
    else:
        root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cls.defaults_file(), settings)
        print(f"created {settings} (edit it to adjust thresholds, timings and dialogue rules)")
    print(f"next: python -m autokit calibrate {cls.id} --profile {args.profile}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    from autokit.calibration.wizard import run_wizard
    from autokit.vision.capture import MssScreen

    cls = resolve(args.automation)
    profile = load_profile(cls, args.profile)
    screen = MssScreen(args.monitor)
    only = [n.strip() for n in args.only.split(",")] if args.only else None
    run_wizard(cls, profile, screen.grab, only=only, delay=args.delay)
    missing = cls.missing_assets(profile)
    if missing:
        print("\nstill missing (required): " + ", ".join(s.name for s in missing))
    else:
        print("\nall required assets are calibrated; verify with `check` before `run`")
    return 0


def cmd_set_color(args: argparse.Namespace) -> int:
    from autokit.vision.color import ColorSpec

    cls = resolve(args.automation)
    profile = load_profile(cls, args.profile)
    names = {s.name for s in cls.assets if s.kind == "color"}
    if args.name not in names:
        raise ConfigError(f"'{args.name}' is not a colour asset of {cls.id}: {', '.join(sorted(names))}")
    r, g, b = (int(v) for v in args.rgb.split(","))
    profile.set_color(args.name, ColorSpec((r, g, b), args.tolerance))
    profile.save_calibration()
    print(f"{args.name} = RGB({r},{g},{b}) ±{args.tolerance}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    import cv2

    from autokit.clock import RealClock
    from autokit.runtime.runner import RunEnvironment, build_context
    from autokit.vision.capture import ImageScreen, MssScreen

    cls = resolve(args.automation)
    profile = load_profile(cls, args.profile)
    screen = ImageScreen.from_file(Path(args.image)) if args.image else MssScreen(args.monitor)
    if args.hover:
        from autokit.input.backend import PynputBackend

        backend = PynputBackend()
    else:
        backend = _NoInput()
    ctx = build_context(profile, RunEnvironment(RealClock(), screen, backend))
    automation = cls(ctx)
    problems = automation.validate()
    for problem in problems:
        print(f"NOT READY: {problem}")
    frame = ctx.grab()
    lines, annotated = automation.diagnose(frame)
    for line in lines:
        print(line)
    if annotated is not None:
        out = profile.runs_dir / f"check-{time.strftime('%Y%m%d-%H%M%S')}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), annotated)
        print(f"annotated screenshot: {out}")
    if args.hover:
        for label, point in automation.hover_targets(frame):
            print(f"hovering (no click): {label} at {point.rounded()}")
            ctx.input.move_to(point)
            ctx.sleep(1.5)
    return 1 if problems else 0


class _NoInput:
    """Input backend for `check`: refuses to send anything."""

    def position(self) -> tuple[int, int]:
        return (0, 0)

    def move(self, x: int, y: int) -> None:
        raise RuntimeError("check does not send input")

    def button(self, button: str, down: bool) -> None:
        raise RuntimeError("check does not send input")

    def key(self, key: str, down: bool) -> None:
        raise RuntimeError("check does not send input")


def cmd_run(args: argparse.Namespace) -> int:
    from autokit.runtime.runner import live_environment, run_with_environment

    cls = resolve(args.automation)
    profile = load_profile(cls, args.profile)
    env = live_environment(profile, args.monitor)
    print(f"starting {cls.id} in {args.start_delay:.0f}s — switch to the game window now")
    time.sleep(args.start_delay)
    summary = run_with_environment(cls, profile, env)
    _print_summary(summary)
    return 0 if summary.completed else 2


def cmd_simulate(args: argparse.Namespace) -> int:
    from autokit.runtime.runner import RunEnvironment, run_with_environment
    from autokit.testing.sim import SimClock, SimDesktop

    cls = resolve(args.automation)
    bundle = cls.simulation(args.scenario, args.seed)
    if bundle is None:
        raise ConfigError(f"{cls.id} has no simulator")
    clock = SimClock()
    desktop = SimDesktop(bundle.world, clock)
    log_dir = Path(args.log_dir) if args.log_dir else None
    env = RunEnvironment(clock, desktop, desktop, log_dir=log_dir, seed=args.seed)
    summary = run_with_environment(cls, bundle.profile, env)
    _print_summary(summary)
    print("\n=== simulator ===")
    for key, value in bundle.world.report().items():
        print(f"  {key}: {value}")
    return 0 if summary.completed else 2


def cmd_capture(args: argparse.Namespace) -> int:
    from autokit.calibration.wizard import save_screenshot
    from autokit.vision.capture import MssScreen

    screen = MssScreen(args.monitor)
    for remaining in range(int(args.delay), 0, -1):
        print(f"capturing in {remaining}...", flush=True)
        time.sleep(1)
    out = Path(args.out or f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png")
    print(f"saved {save_screenshot(screen.grab(), out)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autokit", description="Visual automation runner")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    def with_profile(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("automation")
        p.add_argument("--profile", default="default")
        return p

    with_profile(sub.add_parser("init")).set_defaults(func=cmd_init)

    p = with_profile(sub.add_parser("calibrate"))
    p.add_argument("--only", help="comma-separated asset names to (re)calibrate")
    p.add_argument("--delay", type=float, default=3.0)
    p.add_argument("--monitor", type=int, default=1)
    p.set_defaults(func=cmd_calibrate)

    p = with_profile(sub.add_parser("set-color"))
    p.add_argument("name")
    p.add_argument("rgb", help="R,G,B e.g. 0,255,255")
    p.add_argument("--tolerance", type=int, default=12)
    p.set_defaults(func=cmd_set_color)

    p = with_profile(sub.add_parser("check"))
    p.add_argument("--image", help="analyse a saved screenshot instead of the screen")
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--hover", action="store_true", help="move the mouse over detected targets (never clicks)")
    p.set_defaults(func=cmd_check)

    p = with_profile(sub.add_parser("run"))
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--start-delay", type=float, default=5.0)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("simulate")
    p.add_argument("automation")
    p.add_argument("--scenario", default="default")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--log-dir")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("capture")
    p.add_argument("--delay", type=float, default=3.0)
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--out")
    p.set_defaults(func=cmd_capture)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
