"""Deterministic simulation support: run real automation code against a
simulated world with a virtual clock (no display, no real input)."""

from autokit.testing.sim import InputEvent, SimClock, SimDesktop, SimulationBundle, SimWorld

__all__ = ["InputEvent", "SimClock", "SimDesktop", "SimWorld", "SimulationBundle"]
