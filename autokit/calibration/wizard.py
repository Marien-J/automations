"""Interactive calibration: capture the reference images, colours and regions
an automation declares in its ``assets``.

For each asset the wizard prints instructions, takes a screenshot after a short
countdown (so you can switch to the game and put it in the right state, e.g.
with a right-click menu open), then lets you drag a box around the element:
first roughly on the whole screenshot, then precisely on a zoomed view.

Regions are stored relative to the anchor template, so they remain valid when
the game window is moved.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
import numpy as np

from autokit.geometry import Rect
from autokit.runtime.automation import AssetSpec, Automation
from autokit.runtime.profile import Profile
from autokit.vision.color import ColorSpec, dominant_color
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_best

MAX_VIEW_WIDTH = 1500
ZOOM_TARGET_WIDTH = 900


def pick_marker_color(crop_bgr: np.ndarray) -> tuple[int, int, int]:
    """The colour of a highlight outline inside a small sample box.

    Marker colours are vivid, so prefer the most frequent saturated colour and
    fall back to the most frequent colour overall.
    """
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 80)
    if vivid.any():
        return dominant_color(crop_bgr[vivid].reshape(-1, 1, 3))
    return dominant_color(crop_bgr)


def _select(image: np.ndarray, title: str) -> tuple[int, int, int, int] | None:
    """Two-stage ROI selection; returns (x, y, w, h) in image pixels."""
    scale = min(1.0, MAX_VIEW_WIDTH / image.shape[1])
    view = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    x, y, w, h = cv2.selectROI(f"{title} - drag a box, ENTER to accept, c to cancel", view, showCrosshair=False)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        return None
    x, y, w, h = (int(round(v / scale)) for v in (x, y, w, h))
    pad = 6
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(image.shape[1], x + w + pad), min(image.shape[0], y + h + pad)
    area = image[y0:y1, x0:x1]
    zoom = max(1, min(12, ZOOM_TARGET_WIDTH // max(1, area.shape[1])))
    big = cv2.resize(area, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    fx, fy, fw, fh = cv2.selectROI(f"{title} - refine (zoom x{zoom}), ENTER to accept", big, showCrosshair=False)
    cv2.destroyAllWindows()
    if fw == 0 or fh == 0:
        return x, y, w, h
    return x0 + fx // zoom, y0 + fy // zoom, max(1, fw // zoom), max(1, fh // zoom)


def _anchor_offset(profile: Profile, anchor: str | None, frame: Frame) -> tuple[int, int] | None:
    if anchor is None or not profile.templates.has(anchor) or profile.anchor_rect is None:
        return None
    match = find_best(frame, profile.templates.get(anchor))
    if match is None:
        return None
    return match.rect.x - profile.anchor_rect.x, match.rect.y - profile.anchor_rect.y


def store_asset(profile: Profile, spec: AssetSpec, frame: Frame, rect: Rect, anchor: str | None) -> str:
    """Save one calibrated asset from ``rect`` (screen coordinates) of ``frame``."""
    crop = frame.crop(rect)
    if crop is None:
        raise ValueError(f"selection {rect} is outside the screenshot")
    if spec.kind == "template":
        profile.templates_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(profile.templates_dir / f"{spec.name}.png"), crop.image)
        profile.templates.add(Template(spec.name, crop.image.copy()))
        if spec.name == anchor:
            profile.set_anchor(spec.name, rect)
            profile.set_template_home(spec.name, rect)
        else:
            dx, dy = _anchor_offset(profile, anchor, frame) or (0, 0)
            profile.set_template_home(spec.name, rect.translate(-dx, -dy))
        result = f"saved template {spec.name}.png ({rect.w}x{rect.h})"
    elif spec.kind == "color":
        rgb = pick_marker_color(crop.image)
        tolerance = spec.default.tolerance if isinstance(spec.default, ColorSpec) else 12
        profile.set_color(spec.name, ColorSpec(rgb, tolerance))
        result = f"saved colour {spec.name} = RGB{rgb} ±{tolerance}"
    elif spec.kind == "region":
        offset = _anchor_offset(profile, anchor, frame)
        if offset is None and anchor is not None:
            result_note = " (anchor not found: stored as-is; calibrate the anchor first)"
            offset = (0, 0)
        else:
            result_note = ""
            offset = offset or (0, 0)
        profile.set_region(spec.name, rect.translate(-offset[0], -offset[1]))
        result = f"saved region {spec.name} = {rect}{result_note}"
    else:
        raise ValueError(f"unknown asset kind {spec.kind!r}")
    profile.save_calibration()
    return result


def run_wizard(
    automation_cls: type[Automation],
    profile: Profile,
    grab: Callable[[], Frame],
    *,
    only: Iterable[str] | None = None,
    delay: float = 3.0,
    ask: Callable[[str], str] = input,
) -> None:
    selected = set(only) if only else None
    specs = [s for s in automation_cls.assets if selected is None or s.name in selected]
    anchor = automation_cls.anchor_template
    specs.sort(key=lambda s: s.name != anchor)  # the anchor first: regions depend on it
    for name in sorted((selected or set()) - {s.name for s in specs}):
        # Labels referenced only by user-written rules (e.g. a server-specific message).
        specs.append(AssetSpec(name, "template", "Custom label referenced by your settings/rules.", required=False))
    for index, spec in enumerate(specs, 1):
        flag = "required" if spec.required else "optional"
        print(f"\n[{index}/{len(specs)}] {spec.name}  ({spec.kind}, {flag}, group: {spec.group})")
        print(f"    {spec.instructions}")
        prompt = f"    ENTER = screenshot in {delay:.0f}s, 'i PATH' = use an image file, 's' = skip, 'q' = quit: "
        answer = ask(prompt).strip()
        if answer.lower() == "q":
            break
        if answer.lower() == "s":
            continue
        if answer.lower().startswith("i "):
            image = cv2.imread(answer[2:].strip(), cv2.IMREAD_COLOR)
            if image is None:
                print("    could not read that image; skipped")
                continue
            frame = Frame(image)
        else:
            for remaining in range(int(delay), 0, -1):
                print(f"    capturing in {remaining}...", flush=True)
                time.sleep(1)
            frame = grab()
        box = _select(frame.image, spec.name)
        if box is None:
            print("    nothing selected; skipped")
            continue
        x, y, w, h = box
        print("    " + store_asset(profile, spec, frame, Rect(frame.ox + x, frame.oy + y, w, h), anchor))


def save_screenshot(frame: Frame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), frame.image)
    return path
