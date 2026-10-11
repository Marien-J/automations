"""Verified click helpers shared by automations."""

from __future__ import annotations

import random

from autokit.errors import RecoverableError
from autokit.geometry import Point, Rect
from autokit.runtime.automation import Context
from autokit.vision.frame import Frame
from autokit.vision.templates import Match, Template, find_all, still_at


def point_in_rect(rect: Rect, rng: random.Random, inset: float = 0.3) -> Point:
    """A random point in the central part of ``rect`` (``inset`` trimmed per side)."""
    mx, my = rect.w * inset, rect.h * inset
    return Point(rng.uniform(rect.x + mx, rect.right - mx), rng.uniform(rect.y + my, rect.bottom - my))


def find_unique(ctx: Context, template: Template, region: Rect | None) -> Match:
    """The single match of ``template``; anything else is a recoverable failure."""
    matches = find_all(ctx.grab(region), template, region)
    if not matches:
        raise RecoverableError(f"'{template.name}' is not visible")
    if len(matches) > 1:
        raise RecoverableError(f"'{template.name}' is ambiguous ({len(matches)} matches)")
    return matches[0]


def click_match(ctx: Context, template: Template, match: Match, *, button: str = "left") -> Frame:
    """Move onto ``match`` and click it, provided it is still there on arrival.

    Returns the frame captured just before the click, so callers can detect
    the screen's response to the click itself.
    """
    ctx.input.move_to(point_in_rect(match.rect, ctx.rng))
    frame = ctx.grab()
    if not still_at(frame, template, match.rect):
        raise RecoverableError(f"'{template.name}' changed while the mouse approached it")
    ctx.input.click(button=button)
    return frame


def park_cursor_outside(ctx: Context, area: Rect, gap: tuple[int, int] = (18, 45)) -> None:
    """Move the cursor just above ``area`` so hover effects don't alter its contents."""
    bounds = ctx.screen.bounds
    here = ctx.input.position
    y = area.y - ctx.rng.randint(*gap)
    if y < bounds.y:
        y = area.bottom + ctx.rng.randint(*gap)
    x = min(max(here.x + ctx.rng.uniform(-25, 25), bounds.x + 2), bounds.right - 3)
    ctx.input.move_to(Point(x, min(max(y, bounds.y + 2), bounds.bottom - 3)))


def click_template(ctx: Context, template: Template, region: Rect | None, *, button: str = "left") -> Match:
    """Click a uniquely visible template, re-verifying it right before the click."""
    match = find_unique(ctx, template, region)
    click_match(ctx, template, match, button=button)
    return match
