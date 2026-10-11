"""A captured screen image that knows where it sits on the screen."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import cv2
import numpy as np

from autokit.geometry import Point, Rect


@dataclass(frozen=True)
class Frame:
    """BGR ``uint8`` image whose pixel (0, 0) is at screen position ``(ox, oy)``."""

    image: np.ndarray
    ox: int = 0
    oy: int = 0
    timestamp: float = 0.0

    @cached_property
    def gray(self) -> np.ndarray:
        return cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)

    @property
    def rect(self) -> Rect:
        h, w = self.image.shape[:2]
        return Rect(self.ox, self.oy, w, h)

    def crop(self, region: Rect | None) -> Frame | None:
        """Sub-frame for a screen-space ``region`` (clipped); ``None`` if disjoint."""
        if region is None:
            return self
        clipped = self.rect.intersection(region)
        if clipped is None:
            return None
        x0, y0 = clipped.x - self.ox, clipped.y - self.oy
        sub = self.image[y0 : y0 + clipped.h, x0 : x0 + clipped.w]
        return Frame(sub, clipped.x, clipped.y, self.timestamp)

    def to_screen(self, x: float, y: float) -> Point:
        return Point(self.ox + x, self.oy + y)

    def pixel(self, p: Point) -> tuple[int, int, int]:
        """RGB colour at a screen point."""
        x, y = int(p.x) - self.ox, int(p.y) - self.oy
        b, g, r = self.image[y, x]
        return int(r), int(g), int(b)
