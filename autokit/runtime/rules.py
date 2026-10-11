"""Rule-driven handling of dialogues and other multi-step UI conversations.

Dialogue wording differs between servers and versions, and conversations
branch (a wage request may interrupt an order, a "repeat last task" option may
or may not be offered). Instead of a fixed click sequence, a conversation is a
list of rules, each saying *when these labels are visible (and those are not),
do exactly this*. The engine repeatedly:

1. captures the screen and checks the caller's ``done`` condition,
2. picks the first rule whose condition holds and performs its single action,
3. waits until the screen visibly changes before looking again.

If no rule applies the engine waits briefly (text may still be appearing) and
then calls ``stalled`` — which must raise — instead of guessing. A rule that
keeps firing without progress is stopped by ``max_uses``.

Rules are plain data (YAML), e.g.::

    - {name: pay wages, when: [wage_request, opt_pay], click: opt_pay, signal: wage_paid}
    - {name: quantity, when: [enter_amount], type: "{quantity}", enter: true}
    - {name: no coins, when: [not_enough_coins], key: space, fail: "cannot pay wages"}
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from autokit.errors import AutomationError, ConfigError, RecoverableError, WaitTimeout
from autokit.geometry import Rect
from autokit.runtime.actions import click_match, park_cursor_outside
from autokit.runtime.automation import Context
from autokit.vision.frame import Frame
from autokit.vision.templates import Match, TemplateLibrary, find_all

Visible = dict[str, list[Match]]

_ACTIONS = ("click", "key", "type")


class RuleFailed(AutomationError):
    """A rule declared a terminal outcome (e.g. "insufficient coins")."""

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(reason)
        self.rule = rule


@dataclass(frozen=True)
class Rule:
    name: str
    when: tuple[str, ...]
    unless: tuple[str, ...] = ()
    click: str | None = None
    key: str | None = None
    type: str | None = None
    enter: bool = False
    fail: str | None = None
    max_uses: int = 3
    signal: str | None = None  # reported to the caller's ``on_rule`` callback once performed

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Rule:
        allowed = {"name", "when", "unless", "click", "key", "type", "enter", "fail", "max_uses", "signal"}
        unknown = set(data) - allowed
        name = str(data.get("name", "?"))
        if unknown:
            raise ConfigError(f"rule '{name}': unknown key(s) {sorted(unknown)}")
        when = tuple(data.get("when") or ())
        if not when:
            raise ConfigError(f"rule '{name}' needs a non-empty 'when' list")
        actions = [a for a in _ACTIONS if data.get(a) is not None]
        if len(actions) > 1:
            raise ConfigError(f"rule '{name}' has more than one action: {actions}")
        if not actions and not data.get("fail"):
            raise ConfigError(f"rule '{name}' needs an action ({', '.join(_ACTIONS)}) or 'fail'")
        return cls(
            name=name,
            when=when,
            unless=tuple(data.get("unless") or ()),
            click=data.get("click"),
            key=None if data.get("key") is None else str(data["key"]),
            type=None if data.get("type") is None else str(data["type"]),
            enter=bool(data.get("enter", False)),
            fail=data.get("fail"),
            max_uses=int(data.get("max_uses", 3)),
            signal=data.get("signal"),
        )

    def templates(self) -> set[str]:
        names = set(self.when) | set(self.unless)
        if self.click:
            names.add(self.click)
        return names

    def applies(self, visible: Visible) -> bool:
        return all(visible.get(n) for n in self.when) and not any(visible.get(n) for n in self.unless)


class RuleEngine:
    def __init__(
        self,
        ctx: Context,
        library: TemplateLibrary,
        rules: Sequence[Rule],
        variables: Mapping[str, Any] | None = None,
        *,
        extra_templates: Sequence[str] = (),
    ) -> None:
        self.ctx = ctx
        self.library = library
        self.variables = dict(variables or {})
        # Rules that rely on labels the user has not calibrated cannot be
        # recognised, so they are disabled rather than half-applied.
        self.rules = [r for r in rules if not library.missing(r.templates())]
        self.disabled = [r for r in rules if library.missing(r.templates())]
        names: set[str] = set(extra_templates)
        for rule in self.rules:
            names |= rule.templates()
        self.watched = sorted(n for n in names if library.has(n))

    def visible(self, frame: Frame, region: Rect | None) -> Visible:
        found: Visible = {}
        for name in self.watched:
            matches = find_all(frame, self.library.get(name), region, max_results=3)
            if matches:
                found[name] = matches
        return found

    def select(self, visible: Visible) -> Rule | None:
        for rule in self.rules:
            if rule.applies(visible):
                return rule
        return None

    def run(
        self,
        region: Callable[[], Rect | None],
        *,
        done: Callable[[Frame, Visible], str | None],
        stalled: Callable[[Frame, Visible], None],
        timeout: float,
        step_timeout: float,
        settle_s: float = 1.2,
        on_rule: Callable[[Rule], None] | None = None,
    ) -> str:
        """Drive the conversation until ``done`` returns a result string.

        ``on_rule`` is called right after a rule's input was sent (before its
        effect is confirmed), so callers can record e.g. "order submitted"
        even if confirming the effect later fails.
        """
        ctx = self.ctx
        deadline = ctx.now() + timeout
        uses: Counter[str] = Counter()
        idle_since: float | None = None
        while True:
            area = region()
            frame = ctx.grab()
            visible = self.visible(frame, area)
            result = done(frame, visible)
            if result:
                return result
            if ctx.now() > deadline:
                raise WaitTimeout(f"conversation did not finish within {timeout:.0f}s")
            rule = self.select(visible)
            if rule is None:
                idle_since = ctx.now() if idle_since is None else idle_since
                if ctx.now() - idle_since >= settle_s:
                    stalled(frame, visible)
                    raise RecoverableError("conversation stalled")  # stalled() should already have raised
                ctx.sleep(ctx.poll_interval)
                continue
            idle_since = None
            uses[rule.name] += 1
            if uses[rule.name] > rule.max_uses:
                raise RecoverableError(f"rule '{rule.name}' applied {rule.max_uses} times without progress")
            ctx.log.event("dialogue_rule", rule=rule.name)
            reference = self._perform(rule, visible)
            if on_rule is not None:
                on_rule(rule)
            if rule.fail:
                raise RuleFailed(rule.name, rule.fail.format(**self.variables))
            if reference is not None:
                self._await_change(area, reference, done, step_timeout, rule)
            if rule.click and area is not None:
                park_cursor_outside(ctx, area)

    def _perform(self, rule: Rule, visible: Visible) -> Frame | None:
        """Send the rule's input; returns the frame seen just before it."""
        ctx = self.ctx
        if rule.click:
            matches = visible[rule.click]
            if len(matches) != 1:
                raise RecoverableError(f"rule '{rule.name}': '{rule.click}' is ambiguous ({len(matches)} matches)")
            return click_match(ctx, self.library.get(rule.click), matches[0])
        if rule.key:
            before = ctx.grab()
            ctx.input.press_key(rule.key)
            return before
        if rule.type is not None:
            before = ctx.grab()
            ctx.input.type_text(rule.type.format(**self.variables))
            if rule.enter:
                ctx.input.press_key("enter")
            return before
        return None

    def _await_change(
        self,
        area: Rect | None,
        before: Frame,
        done: Callable[[Frame, Visible], str | None],
        timeout: float,
        rule: Rule,
    ) -> None:
        names_before = set(self.visible(before, area))
        reference = before.crop(area) if area is not None else before

        def changed(frame: Frame) -> bool:
            visible = self.visible(frame, area)
            if done(frame, visible) or set(visible) != names_before:
                return True
            now = frame.crop(area) if area is not None else frame
            if reference is None or now is None or now.image.shape != reference.image.shape:
                return True
            diff = np.abs(now.image.astype(np.int16) - reference.image.astype(np.int16)).max(axis=2)
            return float((diff > 40).mean()) > 0.02

        self.ctx.wait_until(changed, timeout, f"the screen to respond to '{rule.name}'")
