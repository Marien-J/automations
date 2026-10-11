"""Locate the target window through a fixed reference image ("anchor").

Calibrated regions are stored in the screen coordinates seen at calibration
time together with where the anchor was. At runtime the anchor is found again
and every region is shifted by the same offset, so moving the client window
does not require recalibration (resizing it does).
"""

from __future__ import annotations

from dataclasses import dataclass

from autokit.geometry import Rect
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_best


@dataclass(frozen=True)
class AnchorFix:
    rect: Rect
    dx: int
    dy: int


class AnchorTracker:
    def __init__(self, template: Template, calibrated: Rect, search_margin: int = 120) -> None:
        self.template = template
        self.calibrated = calibrated
        self.search_margin = search_margin
        self._last: Rect | None = None

    def locate(self, frame: Frame) -> AnchorFix | None:
        """Search near the last known position first, then the whole frame."""
        areas = []
        if self._last is not None:
            areas.append(self._last.expand(self.search_margin))
        areas.append(None)
        for area in areas:
            match = find_best(frame, self.template, area)
            if match is not None:
                self._last = match.rect
                return AnchorFix(match.rect, match.rect.x - self.calibrated.x, match.rect.y - self.calibrated.y)
        return None
