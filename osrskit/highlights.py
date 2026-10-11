"""Objects and NPCs highlighted by RuneLite plugins (Object Markers, NPC
Indicators, ...). Each marked thing is identified by its outline colour."""

from __future__ import annotations

from dataclasses import dataclass

from autokit.geometry import Point, Rect
from autokit.vision.color import Blob, ColorSpec, find_blobs
from autokit.vision.frame import Frame


@dataclass(frozen=True)
class Pick:
    blob: Blob | None
    candidates: int
    ambiguous: bool = False


@dataclass(frozen=True)
class Highlight:
    color: ColorSpec
    min_pixels: int = 40
    join_px: int = 2
    dominance: float = 3.0  # how much bigger the chosen blob must be than the next one
    near_px: float = 60.0

    def find(self, frame: Frame, region: Rect | None) -> list[Blob]:
        return find_blobs(frame, self.color, region, min_pixels=self.min_pixels, join_px=self.join_px)

    def pick(self, frame: Frame, region: Rect | None, near: Point | None = None) -> Pick:
        """The single marked thing, or an explicit "ambiguous" result.

        With several candidates the biggest wins only if it clearly dominates;
        otherwise the one at the last known position (``near``) wins if exactly
        one is there. Anything else is ambiguous and must not be clicked.
        """
        blobs = self.find(frame, region)
        if not blobs:
            return Pick(None, 0)
        if len(blobs) == 1 or blobs[0].pixels >= self.dominance * blobs[1].pixels:
            return Pick(blobs[0], len(blobs))
        if near is not None:
            close = [b for b in blobs if b.center.distance_to(near) <= self.near_px]
            if len(close) == 1:
                return Pick(close[0], len(blobs))
        return Pick(None, len(blobs), ambiguous=True)
