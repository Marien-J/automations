"""28-slot inventory scanning."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from autokit.geometry import Rect
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_best

COLUMNS = 4
ROWS = 7
SLOTS = COLUMNS * ROWS

EMPTY = "empty"
OTHER = "other"


@dataclass(frozen=True)
class EmptySlotModel:
    """Colour range of the inventory background, learned from one empty slot.

    A slot counts as empty when almost all of its pixels fall inside that
    range; item icons always contain outline/shading pixels outside it.
    """

    lo: np.ndarray
    hi: np.ndarray
    max_outside: float = 0.03

    @classmethod
    def from_sample(cls, image_bgr: np.ndarray, margin: int = 14) -> EmptySlotModel:
        pixels = image_bgr.reshape(-1, 3).astype(np.int16)
        lo = np.percentile(pixels, 2, axis=0) - margin
        hi = np.percentile(pixels, 98, axis=0) + margin
        return cls(lo, hi)

    def is_empty(self, image_bgr: np.ndarray) -> bool:
        pixels = image_bgr.reshape(-1, 3).astype(np.int16)
        outside = np.any((pixels < self.lo) | (pixels > self.hi), axis=1)
        return float(outside.mean()) <= self.max_outside


@dataclass(frozen=True)
class InventoryScan:
    slots: tuple[str, ...]
    knows_empty: bool

    def count(self, item: str) -> int:
        return sum(1 for s in self.slots if s == item)

    @property
    def free(self) -> int | None:
        return self.count(EMPTY) if self.knows_empty else None

    def summary(self) -> dict[str, int]:
        return dict(Counter(self.slots))


class Inventory:
    def __init__(self, items: Mapping[str, Template], empty: EmptySlotModel | None = None) -> None:
        self.items = dict(items)
        self.empty = empty

    @staticmethod
    def slot_rects(grid: Rect) -> list[Rect]:
        """Slot cells for ``grid`` = top-left of slot 1 to bottom-right of slot 28."""
        cells = []
        for row in range(ROWS):
            y0 = grid.y + round(row * grid.h / ROWS)
            y1 = grid.y + round((row + 1) * grid.h / ROWS)
            for col in range(COLUMNS):
                x0 = grid.x + round(col * grid.w / COLUMNS)
                x1 = grid.x + round((col + 1) * grid.w / COLUMNS)
                cells.append(Rect(x0, y0, x1 - x0, y1 - y0))
        return cells

    def scan(self, frame: Frame, grid: Rect) -> InventoryScan:
        slots = []
        for cell in self.slot_rects(grid):
            name = OTHER
            for item, template in self.items.items():
                if find_best(frame, template, cell.expand(3)) is not None:
                    name = item
                    break
            if name == OTHER and self.empty is not None:
                sub = frame.crop(cell.expand(-3))
                if sub is not None and self.empty.is_empty(sub.image):
                    name = EMPTY
            slots.append(name)
        return InventoryScan(tuple(slots), self.empty is not None)
