"""The right-click ("Choose Option") menu.

The standard menu is drawn as a background-coloured rectangle with a black
"Choose Option" header band and a black border around the options area, so
the options area is a solid block of the background colour with the header
just above it. :meth:`ContextMenu.find` looks for exactly that shape near where
the right-click happened; options are then located by their calibrated label
templates, never by position, so the automation can only select an option
whose text it has positively recognised.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from autokit.errors import RecoverableError
from autokit.geometry import Point, Rect
from autokit.runtime.actions import click_match
from autokit.runtime.automation import Context
from autokit.vision.color import ColorSpec, color_mask
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_all

CLASSIC_MENU_BACKGROUND = ColorSpec((93, 84, 71), 6)


@dataclass(frozen=True)
class MenuStyle:
    background: ColorSpec = CLASSIC_MENU_BACKGROUND
    header: ColorSpec = field(default_factory=lambda: ColorSpec((0, 0, 0), 10))
    min_width: int = 50
    min_height: int = 10  # options area: at least one row
    min_fill: float = 0.5
    header_search_rows: int = 26  # how far above the options area the header may start
    min_header_rows: int = 10
    near_px: int = 40


class ContextMenu:
    def __init__(self, style: MenuStyle | None = None) -> None:
        self.style = style or MenuStyle()

    def find(self, frame: Frame, near: Point | None = None, region: Rect | None = None) -> Rect | None:
        """The open menu's rectangle (closest to ``near`` if given), else ``None``."""
        sub = frame.crop(region)
        if sub is None:
            return None
        s = self.style
        mask = color_mask(sub.image, s.background)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        found: list[Rect] = []
        for label in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[label])
            if w < s.min_width or h < s.min_height or area / (w * h) < s.min_fill:
                continue
            header_top = self._header_top(sub.image, x, y, w)
            if header_top is None:
                continue
            top = header_top - 1
            rect = Rect(sub.ox + x - 2, sub.oy + top, w + 4, y + h + 2 - top)
            if near is not None and not rect.expand(s.near_px).contains(near):
                continue
            found.append(rect)
        if not found:
            return None
        if near is not None:
            return min(found, key=lambda r: r.center.distance_to(near))
        return max(found, key=lambda r: r.area)

    def _header_top(self, image: np.ndarray, x: int, y: int, w: int) -> int | None:
        """Topmost row of the dark header band above an options area, if present."""
        s = self.style
        y0 = max(0, y - s.header_search_rows)
        band = image[y0:y, x : x + w]
        if band.shape[0] == 0:
            return None
        dark_rows = np.mean(color_mask(band, s.header) > 0, axis=1) >= 0.5
        if int(dark_rows.sum()) < s.min_header_rows:
            return None
        return y0 + int(np.argmax(dark_rows))

    def option(self, frame: Frame, menu: Rect, template: Template) -> list:
        return find_all(frame, template, menu.expand(2), max_results=3)


def dismiss_menu(ctx: Context, menu: ContextMenu, rect: Rect, timeout: float = 1.5) -> None:
    """Close a menu without selecting anything: move the cursor away from it."""
    bounds = ctx.screen.bounds
    y = ctx.rng.uniform(rect.y + 10, max(rect.y + 11, rect.bottom - 10))
    left = rect.x - ctx.rng.randint(40, 70)
    right = rect.right + ctx.rng.randint(40, 70)
    x = left if left > bounds.x + 5 else right
    if not (bounds.x + 5 < x < bounds.right - 5):
        x, y = rect.center.x, max(bounds.y + 5, rect.y - ctx.rng.randint(40, 70))
    ctx.input.move_to(Point(x, y))
    ctx.wait_until(lambda f: menu.find(f, near=rect.center) is None, timeout, "the right-click menu to close")


def choose_option(
    ctx: Context,
    menu: ContextMenu,
    at: Point,
    option: Template,
    *,
    timeout: float = 1.5,
) -> None:
    """Right-click ``at`` and select ``option``, verifying each step.

    If the menu does not contain exactly one ``option`` entry it is dismissed
    without clicking anything and a :class:`RecoverableError` is raised.
    """
    ctx.input.right_click(at)
    rect = ctx.wait_until(lambda f: menu.find(f, near=at), timeout, "the right-click menu to open")
    matches = menu.option(ctx.grab(), rect, option)
    if len(matches) != 1:
        dismiss_menu(ctx, menu, rect)
        what = "is missing" if not matches else f"appears {len(matches)} times"
        raise RecoverableError(f"menu option '{option.name}' {what}")
    click_match(ctx, option, matches[0])
    ctx.wait_until(lambda f: menu.find(f, near=at) is None, timeout, "the right-click menu to close")
