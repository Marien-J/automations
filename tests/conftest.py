from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from autokit.runtime.automation import RunSummary
from autokit.runtime.runner import RunEnvironment, run_with_environment
from autokit.safety import EmergencyStop
from autokit.testing.sim import SimClock, SimDesktop
from automations.osrs_construction_mahogany_tables.automation import MahoganyTables
from automations.osrs_construction_mahogany_tables.sim import PohWorld, Scenario, build_simulation


@dataclass
class SimResult:
    summary: RunSummary
    world: PohWorld
    desktop: SimDesktop
    stop: EmergencyStop
    events: list[dict]

    @property
    def report(self) -> dict:
        return self.world.report()

    def events_named(self, name: str) -> list[dict]:
        return [e for e in self.events if e["event"] == name]

    def stopped_at(self) -> float:
        return self.events_named("run_finished")[-1]["t"]


@pytest.fixture
def run_sim(tmp_path: Path) -> Callable[..., SimResult]:
    """Run the Construction automation against the simulated POH."""

    def run(scenario: Scenario, seed: int = 1, setup: Callable[[PohWorld, EmergencyStop], None] | None = None):
        bundle = build_simulation(scenario, seed, tmp_path / f"profile-{seed}")
        clock = SimClock()
        desktop = SimDesktop(bundle.world, clock)
        stop = EmergencyStop()
        env = RunEnvironment(clock, desktop, desktop, stop=stop, log_dir=tmp_path / f"log-{seed}", seed=seed)
        if setup is not None:
            setup(bundle.world, stop)
        summary = run_with_environment(MahoganyTables, bundle.profile, env)
        import json

        events = [json.loads(line) for line in (tmp_path / f"log-{seed}" / "events.jsonl").read_text().splitlines()]
        return SimResult(summary, bundle.world, desktop, stop, events)

    return run
