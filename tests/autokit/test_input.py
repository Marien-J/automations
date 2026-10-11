import random

import pytest

from autokit.errors import EmergencyStopped
from autokit.geometry import Point
from autokit.input.controller import InputController
from autokit.input.motion import MotionProfile, plan_path
from autokit.safety import EmergencyStop


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.hooks = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        for when, fn in list(self.hooks):
            if self.t >= when:
                self.hooks.remove((when, fn))
                fn()


class FakeBackend:
    def __init__(self):
        self.pos = (0, 0)
        self.events = []

    def position(self):
        return self.pos

    def move(self, x, y):
        self.pos = (x, y)
        self.events.append(("move", x, y))

    def button(self, b, down):
        self.events.append(("button", b, down))

    def key(self, k, down):
        self.events.append(("key", k, down))


def test_paths_end_exactly_on_target_with_bounded_bend():
    rng = random.Random(3)
    profile = MotionProfile()
    for _ in range(200):
        start = Point(rng.uniform(0, 1000), rng.uniform(0, 700))
        end = Point(rng.uniform(0, 1000), rng.uniform(0, 700))
        path = plan_path(start, end, profile, rng)
        assert path[-1][:2] == end.rounded()
        duration = sum(dt for _, _, dt in path)
        assert duration <= profile.max_move_s + 1e-6
        dist = start.distance_to(end)
        if dist > 3:
            # every point stays near the straight line (bend is bounded)
            limit = min(profile.curvature * dist, profile.max_bend_px) + 2
            dx, dy = (end.x - start.x) / dist, (end.y - start.y) / dist
            for x, y, _ in path:
                off = abs((x - start.x) * dy - (y - start.y) * dx)
                assert off <= limit


def test_no_input_after_emergency_stop():
    clock, backend, stop = FakeClock(), FakeBackend(), EmergencyStop()
    ctl = InputController(backend, clock, stop, random.Random(1))
    ctl.click(Point(300, 300))
    sent = len(backend.events)
    stop.trigger("test")
    with pytest.raises(EmergencyStopped):
        ctl.click(Point(10, 10))
    with pytest.raises(EmergencyStopped):
        ctl.press_key("1")
    assert len(backend.events) == sent


def test_stop_during_a_movement_halts_it_immediately():
    clock, backend, stop = FakeClock(), FakeBackend(), EmergencyStop()
    ctl = InputController(backend, clock, stop, random.Random(1))
    clock.hooks.append((0.05, lambda: stop.trigger("hotkey")))
    with pytest.raises(EmergencyStopped):
        ctl.click(Point(900, 600))
    assert not any(e[0] == "button" for e in backend.events)
    assert backend.pos != (900, 600)


def test_user_moving_the_mouse_stops_the_run():
    clock, backend, stop = FakeClock(), FakeBackend(), EmergencyStop()
    ctl = InputController(backend, clock, stop, random.Random(1), override_tolerance_px=30)
    ctl.move_to(Point(200, 200))
    backend.pos = (420, 260)  # the user grabbed the mouse
    with pytest.raises(EmergencyStopped):
        ctl.click(Point(250, 250))
    assert stop.triggered and "manual mouse movement" in stop.reason
    assert not any(e[0] == "button" for e in backend.events)


def test_button_is_released_even_if_stopped_while_held():
    clock, backend, stop = FakeClock(), FakeBackend(), EmergencyStop()
    ctl = InputController(backend, clock, stop, random.Random(1))
    ctl.move_to(Point(50, 50))
    clock.hooks.append((clock.t + 0.13, lambda: stop.trigger("hotkey")))
    with pytest.raises(EmergencyStopped):
        ctl.click()
    buttons = [e for e in backend.events if e[0] == "button"]
    assert buttons in ([], [("button", "left", True), ("button", "left", False)])
