"""Short, data-driven UI click sequences (e.g. "open settings, House Options,
Call Butler, back to inventory").

Each step performs one verified action and can wait for a template to appear::

    - {click: tab_settings, wait_for: settings_house_options}
    - {interact: bell_pull, option: menu_ring_bell_pull}
    - {key: escape}
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autokit.errors import ConfigError, RecoverableError
from autokit.geometry import Rect
from autokit.runtime.actions import click_template
from autokit.runtime.automation import Context
from autokit.vision.color import ColorSpec
from autokit.vision.templates import TemplateLibrary, find_best
from osrskit.context_menu import ContextMenu, choose_option
from osrskit.highlights import Highlight


@dataclass(frozen=True)
class Step:
    click: str | None = None
    key: str | None = None
    interact: str | None = None  # colour asset of a highlighted object to right-click
    option: str | None = None  # menu option template used with ``interact``
    wait_for: str | None = None
    timeout_s: float = 3.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Step:
        unknown = set(data) - {"click", "key", "interact", "option", "wait_for", "timeout_s"}
        if unknown:
            raise ConfigError(f"step {dict(data)}: unknown key(s) {sorted(unknown)}")
        step = cls(**data)
        actions = [a for a in (step.click, step.key, step.interact) if a]
        if len(actions) != 1:
            raise ConfigError(f"step {dict(data)} needs exactly one of click/key/interact")
        if step.interact and not step.option:
            raise ConfigError(f"step {dict(data)}: 'interact' needs an 'option' menu template")
        return step

    def templates(self) -> set[str]:
        return {n for n in (self.click, self.option, self.wait_for) if n}


def run_steps(
    ctx: Context,
    steps: Sequence[Step],
    library: TemplateLibrary,
    *,
    menu: ContextMenu,
    color: Callable[[str], ColorSpec | None],
    viewport: Rect | None,
    area: Callable[[str], Rect | None] = lambda name: None,
) -> None:
    """Run ``steps``; ``area(name)`` may narrow where a template is searched."""
    for step in steps:
        if step.click:
            click_template(ctx, library.get(step.click), area(step.click))
        elif step.key:
            ctx.input.press_key(step.key)
        elif step.interact:
            spec = color(step.interact)
            if spec is None:
                raise ConfigError(f"colour '{step.interact}' is not calibrated")
            pick = Highlight(spec).pick(ctx.grab(), viewport)
            if pick.blob is None:
                raise RecoverableError(f"'{step.interact}' is not visible" + (" (ambiguous)" if pick.ambiguous else ""))
            choose_option(ctx, menu, pick.blob.pick_point(ctx.rng), library.get(step.option or ""))
        if step.wait_for:
            template = library.get(step.wait_for)
            where = area(step.wait_for)
            ctx.wait_until(
                lambda f, t=template, r=where: find_best(f, t, r), step.timeout_s, f"'{step.wait_for}' to appear"
            )
