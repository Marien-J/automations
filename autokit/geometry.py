"""Screen geometry primitives. All coordinates are absolute screen pixels."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def offset(self, dx: float, dy: float) -> Point:
        return Point(self.x + dx, self.y + dy)

    def distance_to(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def rounded(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_corners(cls, x1: int, y1: int, x2: int, y2: int) -> Rect:
        """Rect spanning [x1, x2) x [y1, y2)."""
        return cls(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    @classmethod
    def from_list(cls, values: Sequence[int]) -> Rect:
        if len(values) != 4:
            raise ValueError(f"expected [x, y, w, h], got {values!r}")
        x, y, w, h = (int(v) for v in values)
        return cls(x, y, w, h)

    def to_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> Point:
        return Point(self.x + self.w / 2, self.y + self.h / 2)

    @property
    def area(self) -> int:
        return self.w * self.h

    def contains(self, p: Point) -> bool:
        return self.x <= p.x < self.right and self.y <= p.y < self.bottom

    def intersection(self, other: Rect) -> Rect | None:
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def translate(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def expand(self, px: int) -> Rect:
        """Grow (or shrink, for negative ``px``) on every side."""
        w = max(0, self.w + 2 * px)
        h = max(0, self.h + 2 * px)
        return Rect(self.x - px, self.y - px, w, h)

    def __str__(self) -> str:
        return f"({self.x},{self.y} {self.w}x{self.h})"
