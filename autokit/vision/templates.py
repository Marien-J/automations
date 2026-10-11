"""Template matching for UI elements, labels and item icons.

Templates are small reference images captured from the user's own client
during calibration (``autokit calibrate``). Matching uses normalised
cross-correlation plus a mean-colour-difference check, so a label only matches
when both its shape *and* its colours agree (e.g. a greyed-out or red
"unavailable" entry does not match the normal one).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autokit.errors import ConfigError
from autokit.geometry import Point, Rect
from autokit.vision.frame import Frame

DEFAULT_THRESHOLD = 0.9
DEFAULT_MAX_COLOR_DIFF = 30.0
DEFAULT_MAX_MISMATCH = 0.08
_DIFFERENT = 60  # per-channel difference that makes two pixels "different"
_INK = 40  # difference from the background that makes a pixel part of the drawing
ISOLATION_STRIP_PX = 7
ISOLATION_COLOR_TOLERANCE = 40


@dataclass(frozen=True)
class Template:
    """A reference image.

    ``isolated`` templates are whole labels: a match is rejected unless the
    pixels just left and right of it are plain background, so "Mahogany planks"
    does not match inside "Fetch 24 x Mahogany planks".
    """

    name: str
    image: np.ndarray  # BGR uint8
    threshold: float = DEFAULT_THRESHOLD
    max_color_diff: float = DEFAULT_MAX_COLOR_DIFF
    isolated: bool = False
    max_mismatch: float = DEFAULT_MAX_MISMATCH

    @cached_property
    def gray(self) -> np.ndarray:
        return cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)

    @cached_property
    def background(self) -> np.ndarray:
        return _background(self.image)

    @cached_property
    def foreground(self) -> np.ndarray:
        """Mask of the pixels that differ from the template's background colour.

        Colour agreement is judged on these pixels, so text or an icon drawn in
        a different colour (e.g. a red "unavailable" entry) does not match even
        though it covers only a small part of the template.
        """
        fg = _ink(self.image, self.background)
        share = float(fg.mean())
        return fg if 0.02 <= share <= 0.98 else np.ones(fg.shape, bool)

    def color_diff(self, patch: np.ndarray) -> float:
        """Mean absolute colour difference to ``patch`` over the foreground."""
        diff = np.abs(patch.astype(np.int16) - self.image.astype(np.int16)).mean(axis=2)
        return float(diff[self.foreground].mean())

    def mismatch(self, patch: np.ndarray) -> float:
        """Share of drawn pixels (in either image) whose colours disagree.

        Labels render pixel-identically, so a real match is ~0; a label that
        differs by a single word ("Build Chair space" vs "Build Table space")
        scores far higher even when its overall correlation is high.
        """
        different = np.abs(patch.astype(np.int16) - self.image.astype(np.int16)).max(axis=2) > _DIFFERENT
        drawn = self.foreground | _ink(patch, self.background)
        total = int(drawn.sum())
        return float((different & drawn).sum()) / total if total else 0.0

    def same_shape(self, patch: np.ndarray) -> float:
        """Like :meth:`mismatch` but colour-blind: compares where ink is, not its colour."""
        theirs = _ink(patch, _background(patch))
        union = self.foreground | theirs
        total = int(union.sum())
        return float((self.foreground ^ theirs).sum()) / total if total else 0.0

    def accepts(self, patch: np.ndarray) -> bool:
        return self.color_diff(patch) <= self.max_color_diff and self.mismatch(patch) <= self.max_mismatch

    @cached_property
    def foreground_colors(self) -> np.ndarray:
        """The (up to 4) most common foreground colours, e.g. a label's text colours."""
        pixels = self.image[self.foreground].reshape(-1, 3)
        colors, counts = np.unique(pixels, axis=0, return_counts=True)
        return colors[np.argsort(counts)[::-1][:4]].astype(np.int16)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass(frozen=True)
class Match:
    name: str
    rect: Rect
    score: float

    @property
    def center(self) -> Point:
        return self.rect.center


def _score_map(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Normalised correlation of grayscale images (colour is checked separately)."""
    if float(needle.std()) < 1e-6:
        # Flat template: normalised correlation is undefined, use RMS difference.
        sq = cv2.matchTemplate(haystack.astype(np.float32), needle.astype(np.float32), cv2.TM_SQDIFF)
        rms = np.sqrt(np.maximum(sq, 0) / needle.size)
        return 1.0 - rms / 255.0
    res = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(res, -1.0, 1.0)


def _background(image: np.ndarray) -> np.ndarray:
    """Most common colour on the image border."""
    border = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]])
    colors, counts = np.unique(border.reshape(-1, 3), axis=0, return_counts=True)
    return colors[int(np.argmax(counts))].astype(np.int16)


def _ink(image: np.ndarray, background: np.ndarray) -> np.ndarray:
    return np.abs(image.astype(np.int16) - background).max(axis=2) > _INK


def _has_label_pixels(strip: np.ndarray, colors: np.ndarray) -> bool:
    if strip.size == 0:
        return False
    pixels = strip.reshape(-1, 1, 3).astype(np.int16)
    near = np.abs(pixels - colors[None, :, :]).max(axis=2) <= ISOLATION_COLOR_TOLERANCE
    return bool(near.any())


def _isolated(hay: np.ndarray, x: int, y: int, template: Template) -> bool:
    """No more text of the label's colours directly left or right of the match.

    Borders, icons and backgrounds in other colours are fine; another word in
    the same text colour means the match is part of a longer label.
    """
    w, h = template.width, template.height
    left = hay[y : y + h, max(0, x - ISOLATION_STRIP_PX) : x]
    right = hay[y : y + h, x + w : x + w + ISOLATION_STRIP_PX]
    colors = template.foreground_colors
    return not _has_label_pixels(left, colors) and not _has_label_pixels(right, colors)


def find_all(
    frame: Frame,
    template: Template,
    region: Rect | None = None,
    *,
    threshold: float | None = None,
    max_results: int = 10,
) -> list[Match]:
    """All non-overlapping matches above the threshold, best first."""
    sub = frame.crop(region)
    if sub is None:
        return []
    hay = sub.image
    th, tw = template.height, template.width
    if hay.shape[0] < th or hay.shape[1] < tw:
        return []
    scores = _score_map(sub.gray, template.gray)
    limit = template.threshold if threshold is None else threshold
    matches: list[Match] = []
    while len(matches) < max_results:
        _, best, _, (x, y) = cv2.minMaxLoc(scores)
        if best < limit:
            break
        patch = hay[y : y + th, x : x + tw]
        if template.accepts(patch) and (not template.isolated or _isolated(hay, x, y, template)):
            matches.append(Match(template.name, Rect(sub.ox + x, sub.oy + y, tw, th), float(best)))
        y0, x0 = max(0, y - th // 2), max(0, x - tw // 2)
        scores[y0 : y + th // 2 + 1, x0 : x + tw // 2 + 1] = -1.0
    return matches


def find_best(frame: Frame, template: Template, region: Rect | None = None) -> Match | None:
    found = find_all(frame, template, region, max_results=1)
    return found[0] if found else None


def still_at(frame: Frame, template: Template, rect: Rect) -> bool:
    """Whether ``template`` is still shown exactly at ``rect``.

    Accepts an exact match, or exactly the same shape in different colours:
    many UIs recolour an option while the cursor hovers over it (OSRS dialogue
    options turn white), which must not count as "the screen changed".
    """
    sub = frame.crop(rect)
    if sub is None or sub.image.shape[:2] != template.image.shape[:2]:
        return False
    if template.accepts(sub.image):
        return True
    return template.same_shape(sub.image) <= template.max_mismatch


class TemplateLibrary:
    """Named templates loaded from a profile's ``templates/`` directory."""

    def __init__(self, templates: Mapping[str, Template] | None = None) -> None:
        self._templates: dict[str, Template] = dict(templates or {})

    @classmethod
    def load(cls, directory: Path, overrides: Mapping[str, Mapping[str, Any]] | None = None) -> TemplateLibrary:
        overrides = overrides or {}
        templates: dict[str, Template] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.png")):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is None:
                    raise ConfigError(f"could not read template image {path}")
                opts = overrides.get(path.stem, {})
                templates[path.stem] = Template(
                    path.stem,
                    image,
                    float(opts.get("threshold", DEFAULT_THRESHOLD)),
                    float(opts.get("max_color_diff", DEFAULT_MAX_COLOR_DIFF)),
                    bool(opts.get("isolated", False)),
                    float(opts.get("max_mismatch", DEFAULT_MAX_MISMATCH)),
                )
        return cls(templates)

    def add(self, template: Template) -> None:
        self._templates[template.name] = template

    def has(self, name: str) -> bool:
        return name in self._templates

    def get(self, name: str) -> Template:
        try:
            return self._templates[name]
        except KeyError:
            raise ConfigError(
                f"template '{name}' is not calibrated; run `python -m autokit calibrate <automation> --only {name}`"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._templates)

    def missing(self, names: Iterable[str]) -> list[str]:
        return [n for n in names if n not in self._templates]
