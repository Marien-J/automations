"""Smooth, bounded mouse paths and input pacing.

Paths are cubic Bézier curves with a modest sideways bend, traversed with a
minimum-jerk speed profile (slow start, fast middle, slow arrival). Duration
grows with distance like Fitts' law. All variation is bounded and exists for
smooth, natural-looking interaction — correctness never depends on it: the
final point is always exactly the requested target.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from autokit.geometry import Point


@dataclass(frozen=True)
class MotionProfile:
    min_move_s: float = 0.10
    max_move_s: float = 0.60
    base_move_s: float = 0.08
    per_bit_s: float = 0.075
    step_s: float = 0.012
    speed_variation: float = 0.12
    curvature: float = 0.15
    max_bend_px: float = 60.0
    pre_click_s: tuple[float, float] = (0.04, 0.12)
    click_hold_s: tuple[float, float] = (0.045, 0.11)
    post_click_s: tuple[float, float] = (0.06, 0.16)
    key_hold_s: tuple[float, float] = (0.04, 0.09)
    between_keys_s: tuple[float, float] = (0.05, 0.13)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> MotionProfile:
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown mouse setting(s): {sorted(unknown)}")
        kwargs = {k: tuple(v) if isinstance(v, list) else v for k, v in values.items()}
        return cls(**kwargs)


def move_duration(distance: float, profile: MotionProfile, rng: random.Random) -> float:
    base = profile.base_move_s + profile.per_bit_s * math.log2(1.0 + distance / 10.0)
    base *= 1.0 + rng.uniform(-profile.speed_variation, profile.speed_variation)
    return min(profile.max_move_s, max(profile.min_move_s, base))


def _min_jerk(t: float) -> float:
    return t * t * t * (10 - 15 * t + 6 * t * t)


def plan_path(start: Point, end: Point, profile: MotionProfile, rng: random.Random) -> list[tuple[int, int, float]]:
    """Steps ``(x, y, seconds_to_wait_after)``; the last step is exactly ``end``."""
    distance = start.distance_to(end)
    ex, ey = end.rounded()
    if distance < 3:
        return [(ex, ey, profile.step_s)]
    duration = move_duration(distance, profile, rng)
    steps = max(2, int(duration / profile.step_s))
    dx, dy = (end.x - start.x) / distance, (end.y - start.y) / distance
    px, py = -dy, dx  # unit perpendicular
    bend = min(profile.curvature * distance, profile.max_bend_px)
    b1 = rng.uniform(-1.0, 1.0) * bend
    b2 = b1 * rng.uniform(0.2, 1.0)
    c1 = Point(start.x + (end.x - start.x) * 0.3 + px * b1, start.y + (end.y - start.y) * 0.3 + py * b1)
    c2 = Point(start.x + (end.x - start.x) * 0.7 + px * b2, start.y + (end.y - start.y) * 0.7 + py * b2)
    dt = duration / steps
    path: list[tuple[int, int, float]] = []
    for i in range(1, steps + 1):
        t = _min_jerk(i / steps)
        u = 1 - t
        x = u**3 * start.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t**3 * end.x
        y = u**3 * start.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t**3 * end.y
        point = (int(round(x)), int(round(y)))
        if path and path[-1][:2] == point:
            path[-1] = (point[0], point[1], path[-1][2] + dt)
        else:
            path.append((point[0], point[1], dt))
    path[-1] = (ex, ey, path[-1][2])
    return path
