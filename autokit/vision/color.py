"""Colour masks and blob detection.

RuneLite highlight plugins (Object Markers, NPC Indicators, ...) draw outlines
in a single, user-chosen colour. :func:`find_blobs` turns such an outline into
a filled shape so that a click point can be chosen well inside the object.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from autokit.geometry import Point, Rect
from autokit.vision.frame import Frame


@dataclass(frozen=True)
class ColorSpec:
    """An RGB colour with a per-channel tolerance."""

    rgb: tuple[int, int, int]
    tolerance: int = 12

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | Sequence[int]) -> ColorSpec:
        if isinstance(value, Mapping):
            r, g, b = value["rgb"]
            return cls((int(r), int(g), int(b)), int(value.get("tolerance", 12)))
        r, g, b = value
        return cls((int(r), int(g), int(b)))

    def to_dict(self) -> dict[str, Any]:
        return {"rgb": list(self.rgb), "tolerance": self.tolerance}

    def bounds_bgr(self) -> tuple[np.ndarray, np.ndarray]:
        r, g, b = self.rgb
        center = np.array([b, g, r], dtype=np.int16)
        lo = np.clip(center - self.tolerance, 0, 255).astype(np.uint8)
        hi = np.clip(center + self.tolerance, 0, 255).astype(np.uint8)
        return lo, hi


def color_mask(image_bgr: np.ndarray, spec: ColorSpec) -> np.ndarray:
    """``uint8`` mask (0/255) of pixels within ``spec``'s tolerance."""
    lo, hi = spec.bounds_bgr()
    return cv2.inRange(image_bgr, lo, hi)


@dataclass(frozen=True)
class Blob:
    """A connected region of a colour, with its filled interior."""

    bbox: Rect
    pixels: int  # pixels that actually matched the colour
    interior: np.ndarray  # bool mask (bbox-local) of the filled shape

    @property
    def fill_area(self) -> int:
        return int(self.interior.sum())

    @property
    def center(self) -> Point:
        ys, xs = np.nonzero(self.interior)
        return Point(self.bbox.x + float(xs.mean()), self.bbox.y + float(ys.mean()))

    def contains(self, p: Point) -> bool:
        x, y = int(p.x) - self.bbox.x, int(p.y) - self.bbox.y
        if not (0 <= x < self.bbox.w and 0 <= y < self.bbox.h):
            return False
        return bool(self.interior[y, x])

    def pick_point(self, rng: random.Random, inner_fraction: float = 0.5) -> Point:
        """A random point deep inside the shape (bounded variation, never on the edge)."""
        mask = self.interior.astype(np.uint8)
        dist = cv2.distanceTransform(np.pad(mask, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
        deepest = float(dist.max())
        if deepest <= 0:
            return self.bbox.center
        candidates = np.argwhere(dist >= deepest * inner_fraction)
        y, x = candidates[rng.randrange(len(candidates))]
        return Point(self.bbox.x + int(x), self.bbox.y + int(y))


def find_blobs(
    frame: Frame,
    spec: ColorSpec,
    region: Rect | None = None,
    *,
    min_pixels: int = 20,
    join_px: int = 2,
) -> list[Blob]:
    """Blobs of ``spec`` inside ``region``, largest first.

    ``join_px`` closes small gaps so a dashed or anti-aliased outline becomes a
    single shape; the shape's outer contour is then filled.
    """
    sub = frame.crop(region)
    if sub is None:
        return []
    raw = color_mask(sub.image, spec)
    if not raw.any():
        return []
    joined = raw
    if join_px > 0:
        kernel = np.ones((2 * join_px + 1, 2 * join_px + 1), np.uint8)
        joined = cv2.dilate(raw, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    blobs: list[Blob] = []
    for label in range(1, count):
        x, y, w, h = (int(v) for v in stats[label, :4])
        component = (labels[y : y + h, x : x + w] == label).astype(np.uint8)
        pixels = int(np.count_nonzero(raw[y : y + h, x : x + w] & (component * 255)))
        if pixels < min_pixels:
            continue
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(component)
        cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
        blobs.append(Blob(Rect(sub.ox + x, sub.oy + y, w, h), pixels, filled.astype(bool)))
    blobs.sort(key=lambda b: b.pixels, reverse=True)
    return blobs


def dominant_color(image_bgr: np.ndarray) -> tuple[int, int, int]:
    """Most frequent exact colour (RGB) in an image; used during calibration."""
    flat = image_bgr.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    b, g, r = colors[int(np.argmax(counts))]
    return int(r), int(g), int(b)
