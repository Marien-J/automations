"""Build and remove Mahogany tables on one Table space, keeping the inventory
stocked with mahogany planks through the Demon Butler.

Every loop iteration observes the screen once (:class:`Snapshot`), picks the
single most important thing to do (:meth:`MahoganyTables.decide`) and performs
it as a verified procedure: each input is followed by a wait for the expected
visual state, with a timeout only as a safeguard. Unrecognised states stop
the run; failed interactions are retried a bounded number of times.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from autokit.errors import RecoverableError, UnsafeState
from autokit.geometry import Point
from autokit.runtime.actions import click_match, click_template
from autokit.runtime.automation import Automation, Context
from autokit.runtime.rules import Rule, RuleEngine, RuleFailed, Visible
from autokit.vision.color import Blob
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_all
from osrskit.context_menu import choose_option, dismiss_menu
from osrskit.inventory import SLOTS
from osrskit.steps import run_steps

from .assets import ANCHOR, ASSETS
from .perception import PLANK, Dialogue, Perception, Snapshot, Table
from .refill import PlankLedger, Refill
from .settings import Settings

XP_PER_TABLE = 840
REMOVAL_CONFIRM_WINDOW_S = 15.0

Handler = Callable[[Snapshot], None]


class MahoganyTables(Automation):
    id = "osrs_construction_mahogany_tables"
    title = "Construction: Mahogany tables + Demon Butler"
    description = (
        "Builds and removes Mahogany tables on one POH Table space and keeps planks "
        "stocked by sending the Demon Butler to the bank."
    )
    assets = ASSETS
    anchor_template = ANCHOR
    package_dir = Path(__file__).resolve().parent

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.settings = Settings.from_dict(ctx.profile.settings)
        self.max_consecutive_failures = self.settings.safety.max_consecutive_failures
        self.rules: list[Rule] = self.settings.rules()
        self.order_rules: list[Rule] = self.settings.order_rules()
        self.summon_steps = self.settings.summon_steps()
        self.ppt = self.settings.construction.planks_per_table
        self.ledger = PlankLedger()
        self.refill = Refill(self.settings.butler)
        self.counts: Counter[str] = Counter()
        self.build_costs: Counter[int] = Counter()
        self.orders_at_wage: list[int] = []
        self._perception: Perception | None = None
        self._started = ctx.now()
        self._since: dict[str, float] = {}
        self._last_status = -math.inf
        self._removal_started_at: float | None = None
        self._assumed_built = False
        self._last_hotspot_blob: Blob | None = None
        self._wage_offered = False

    # -- setup -------------------------------------------------------------------------
    @property
    def perception(self) -> Perception:
        if self._perception is None:
            self._perception = Perception(self.ctx.profile, self.settings, self.rules, self.order_rules)
        return self._perception

    @classmethod
    def required_groups(cls, settings: Mapping[str, Any]) -> set[str]:
        """``core``, plus the butler and the configured summon method when refills are on."""
        groups = {"core"}
        butler = Settings.from_dict(dict(settings)).butler
        if butler.enabled:
            groups.add("butler")
            if butler.summon_method != "none":
                groups.add(f"summon_{butler.summon_method}")
        return groups

    def validate(self) -> list[str]:
        profile = self.ctx.profile
        library = profile.templates
        problems = [
            f"{s.kind} '{s.name}' is not calibrated"
            for s in self.missing_assets(profile, self.required_groups(profile.settings))
        ]
        if profile.anchor_rect is None:
            problems.append(f"the anchor '{ANCHOR}' has no recorded position; calibrate it first")
        butler = self.settings.butler
        if butler.enabled:
            for step in self.summon_steps:
                for name in library.missing(step.templates()):
                    problems.append(f"summon step needs template '{name}' (calibrate it or edit butler.summon_steps)")
            if butler.talk_mode == "menu" and not library.has("menu_talk_to_butler"):
                problems.append("butler.talk_mode is 'menu' but 'menu_talk_to_butler' is not calibrated")
            submit = [
                r for r in self.order_rules if r.signal == "order_submitted" and not library.missing(r.templates())
            ]
            if not submit:
                problems.append("no usable order rule with signal 'order_submitted' (calibrate prompt_enter_amount)")
        disabled = [r.name for r in self.rules + self.order_rules if library.missing(r.templates())]
        if disabled:
            self.ctx.log.info("dialogue rules skipped (labels not calibrated): %s", ", ".join(disabled))
        return problems

    # -- loop --------------------------------------------------------------------------
    def finished(self) -> str | None:
        run = self.settings.run
        if run.max_tables and self.counts["tables_removed"] >= run.max_tables:
            return f"completed {self.counts['tables_removed']} tables (run.max_tables)"
        if run.max_minutes and self.ctx.now() - self._started >= run.max_minutes * 60:
            return f"ran for {run.max_minutes} minutes (run.max_minutes)"
        return None

    def step(self) -> None:
        snap = self.observe()
        self.decide(snap)(snap)

    def observe(self) -> Snapshot:
        snap = self.perception.observe(self.ctx.grab())
        if snap.client.ok:
            if snap.hotspot.blob is not None:
                self._last_hotspot_blob = snap.hotspot.blob
            self._account(snap)
        self._status(snap)
        return snap

    def decide(self, s: Snapshot) -> Handler:
        """The single most important thing to do for this observation."""
        if not s.client.ok:
            return self._client_problem
        self._since.pop("client", None)
        if s.menu is not None:
            return self._close_stray_menu
        if s.furniture_open:
            return self._close_stray_furniture_menu
        dialogue = s.dialogue
        if dialogue is Dialogue.REMOVE_CONFIRM:
            return self._confirm_pending_removal
        if dialogue is Dialogue.SETTLE:
            return self._settle
        if dialogue is Dialogue.ORDER:
            return self._order if self.refill.can_order() else self._unrecognised_dialogue
        if dialogue is Dialogue.UNKNOWN:
            return self._unrecognised_dialogue
        self._since.pop("dialogue", None)
        if not s.inventory_open:
            return self._reopen_inventory
        now = self.ctx.now()
        if self.refill.overdue(now):
            return self._chase_butler
        if self.refill.wants_collect(self._free_slots(s)):
            return self._collect
        if self.refill.wants_order(s.planks):
            return self._order
        table = self._table(s)
        if table is Table.BUILT:
            return self._remove
        if table is Table.EMPTY:
            self._since.pop("table", None)
            return self._build if s.planks >= self.ppt else self._wait_for_planks
        return self._table_missing

    # -- bookkeeping -------------------------------------------------------------------
    def _account(self, s: Snapshot) -> None:
        now = self.ctx.now()
        if s.inventory is not None and s.inventory_open and s.menu is None:
            delta = self.ledger.observe(s.planks)
            if delta > 0:
                self.counts["deliveries"] += 1
                self.counts["planks_delivered"] += delta
                complete = self.refill.delivered(delta, now)
                trip = self.refill.trip
                self.ctx.log.event(
                    "planks_received",
                    planks=delta,
                    in_inventory=s.planks,
                    still_held_by_butler=0 if trip is None else trip.outstanding,
                    trip_complete=complete,
                )
            elif delta < 0:
                self._unexplained_drop(-delta, s)
        if any(s.visible.get(n) for n in ("butler_returned_msg", "butler_holding_msg")):
            self.refill.mark_returned(now)
        event = self.refill.update(
            now,
            butler_visible=s.butler.blob is not None,
            chat_idle=s.chat_idle,
            departure_timeout=self.settings.timing.departure_timeout_s,
        )
        if event:
            self.ctx.log.event(f"butler_{event}")
            if event == "order_not_taken":
                self.refill.order_failed()

    def _unexplained_drop(self, missing: int, s: Snapshot) -> None:
        if missing == self.ppt and self._table(s) is Table.BUILT:
            # A build whose confirmation timed out completed after all.
            self.counts["tables_built"] += 1
            self.build_costs[missing] += 1
            self._assumed_built = True
            self.ctx.log.event("build_confirmed_late")
            return
        raise UnsafeState(
            f"the plank count dropped by {missing} without a build: the inventory may be covered or unreadable"
        )

    def _status(self, s: Snapshot) -> None:
        now = self.ctx.now()
        if now - self._last_status < self.settings.run.status_every_s:
            return
        self._last_status = now
        trip = self.refill.trip
        self.ctx.log.event(
            "status",
            tables=self.counts["tables_removed"],
            planks=s.planks,
            trip="none" if trip is None else f"{trip.received}/{trip.requested}",
            refills="on" if self.refill.enabled else f"off ({self.refill.disabled_reason})",
            screen=s.describe(),
        )

    def _free_slots(self, s: Snapshot) -> int | None:
        if s.inventory is None:
            return None
        if s.inventory.knows_empty:
            return s.inventory.free
        return SLOTS - s.planks - self.settings.inventory.other_items

    def _table(self, s: Snapshot) -> Table:
        table = self.perception.table_state(s.hotspot, s.built)
        if (
            table is Table.UNKNOWN
            and self.perception.built is None
            and self._assumed_built
            and s.hotspot.blob is None
            and not s.hotspot.ambiguous
        ):
            return Table.BUILT  # no built-table colour: rely on the last verified build
        return table

    def _since_first(self, key: str) -> float:
        return self.ctx.now() - self._since.setdefault(key, self.ctx.now())

    def _template(self, name: str) -> Template:
        return self.ctx.profile.templates.get(name)

    # -- table cycle -------------------------------------------------------------------
    def _build(self, s: Snapshot) -> None:
        ctx, timing = self.ctx, self.settings.timing
        blob = s.hotspot.blob
        assert blob is not None
        before = s.planks
        self._since.pop("waiting", None)
        ctx.log.event("build_start", planks=before)
        choose_option(
            ctx,
            self.perception.menu,
            blob.pick_point(ctx.rng),
            self._template("menu_build_table_space"),
            timeout=timing.menu_timeout_s,
        )
        ctx.wait_until(self.perception.furniture_open, timing.furniture_menu_timeout_s, "the Furniture Creation Menu")
        entry = self._template("furniture_mahogany_table")
        matches = find_all(ctx.grab(), entry, self.perception.viewport)
        if len(matches) != 1:
            self._close_furniture_menu()
            raise RecoverableError(
                "'Mahogany table' is not selectable in the Furniture Creation Menu "
                f"({'not found' if not matches else 'ambiguous'}; level or materials missing?)"
            )
        click_match(ctx, entry, matches[0])
        seen_xp = [not self.settings.construction.require_xp_drop]
        (after,) = ctx.wait_until(
            lambda f: self._built_count(f, before, seen_xp), timing.build_timeout_s, "the Mahogany table to be built"
        )
        self.ledger.consume(self.ppt)
        self._assumed_built = True
        if after <= before:
            self.build_costs[before - after] += 1
        self.counts["tables_built"] += 1
        self.mark_progress()
        ctx.log.event("table_built", planks_left=after, xp_drop_seen=seen_xp[0])

    def _built_count(self, frame: Frame, before: int, seen_xp: list[bool]) -> tuple[int] | None:
        """``(plank count,)`` once every build signal agrees, else ``None``."""
        p = self.perception
        if p.visible(frame, "xp_drop_construction", p.viewport):
            seen_xp[0] = True
        if p.furniture_open(frame) or p.hotspot_pick(frame).blob is not None:
            return None
        built = p.built_pick(frame)
        if built is not None and built.blob is None:
            return None
        count = p.scan_inventory(frame).count(PLANK)
        if count == before or not seen_xp[0]:
            return None
        return (count,)

    def _remove(self, s: Snapshot) -> None:
        ctx, timing = self.ctx, self.settings.timing
        blob = s.built.blob if s.built is not None and s.built.blob is not None else self._last_hotspot_blob
        if blob is None:
            raise UnsafeState("the table's position is unknown")
        self._removal_started_at = ctx.now()
        ctx.log.event("remove_start")
        choose_option(
            ctx,
            self.perception.menu,
            blob.pick_point(ctx.rng),
            self._template("menu_remove_mahogany_table"),
            timeout=timing.menu_timeout_s,
        )
        outcome = ctx.wait_until(self._removal_outcome, timing.remove_timeout_s, "the removal confirmation")
        if outcome == "confirm":
            self._press_removal_confirm()
        self._removal_done()

    def _removal_outcome(self, frame: Frame) -> str | None:
        if self.perception.visible(frame, "remove_confirm", self.perception.chatbox):
            return "confirm"
        return "gone" if self._table_empty(frame) else None

    def _press_removal_confirm(self) -> None:
        # Reached only after `remove_confirm` was recognised in the chatbox.
        self.ctx.input.press_key(self.settings.construction.remove_confirm_key)
        self.ctx.wait_until(self._table_empty, self.settings.timing.remove_timeout_s, "the table to disappear")

    def _removal_done(self) -> None:
        self.counts["tables_removed"] += 1
        self._assumed_built = False
        self._removal_started_at = None
        self.mark_progress()
        self.ctx.log.event("table_removed", cycles=self.counts["tables_removed"])

    def _table_empty(self, frame: Frame) -> bool:
        p = self.perception
        if p.furniture_open(frame) or p.hotspot_pick(frame).blob is None:
            return False
        built = p.built_pick(frame)
        return built is None or built.blob is None

    def _confirm_pending_removal(self, s: Snapshot) -> None:
        started = self._removal_started_at
        if started is None or self.ctx.now() - started > REMOVAL_CONFIRM_WINDOW_S:
            raise UnsafeState("a removal confirmation is open that this automation did not start")
        self._press_removal_confirm()
        self._removal_done()

    def _wait_for_planks(self, s: Snapshot) -> None:
        if self.refill.trip is not None:
            if "waiting" not in self._since:
                self.ctx.log.event("waiting_for_butler", planks=s.planks)
            self._since_first("waiting")
            self.ctx.sleep(self.settings.timing.wait_poll_s)
            return
        self._since.pop("waiting", None)
        if self.refill.can_order():
            self._order(s)  # threshold below one table: order now
            return
        raise UnsafeState(
            f"not enough planks ({s.planks}/{self.ppt}) and refills are off: {self.refill.disabled_reason}"
        )

    # -- butler --------------------------------------------------------------------------
    def _engine(self, rules: list[Rule]) -> RuleEngine:
        return RuleEngine(
            self.ctx,
            self.ctx.profile.templates,
            rules,
            {"quantity": self.settings.butler.request_quantity},
            extra_templates=self.perception.safe_open,
        )

    def _run_conversation(self, rules: list[Rule], done, stalled) -> str:
        t = self.settings.timing
        self._wage_offered = False
        try:
            result = self._engine(rules).run(
                lambda: self.perception.chatbox,
                done=done,
                stalled=stalled,
                timeout=t.dialogue_timeout_s,
                step_timeout=t.dialogue_step_timeout_s,
                settle_s=t.dialogue_settle_s,
                on_rule=self._on_rule,
            )
        finally:
            offered, self._wage_offered = self._wage_offered, False
        if offered:
            # Only a conversation that ended normally confirms the payment
            # (a failed one ends in RuleFailed, e.g. "not enough coins").
            self.counts["wages_paid"] += 1
            self.orders_at_wage.append(self.counts["orders"])
            self.ctx.log.event("wage_paid", after_orders=self.counts["orders"])
        return result

    def _on_rule(self, rule: Rule) -> None:
        now = self.ctx.now()
        if rule.signal == "order_submitted" and self.refill.trip is None:
            self.refill.order_submitted(self.settings.butler.request_quantity, now)
            self.counts["orders"] += 1
            self.ctx.log.event("order_submitted", quantity=self.settings.butler.request_quantity)
        elif rule.signal == "wage_paid":
            self._wage_offered = True
        elif rule.signal == "butler_returned":
            self.refill.mark_returned(now)

    def _rule_failed(self, exc: RuleFailed) -> None:
        trip = self.refill.trip
        self.refill.disable(str(exc))
        if trip is not None:
            self.ctx.log.event("trip_abandoned", planks_left_with_butler=trip.outstanding)
            self.refill.trip = None
        self.ctx.log.event("refills_disabled", reason=str(exc))

    def _settled(self, frame: Frame, visible: Visible) -> str | None:
        """Done condition for non-order conversations."""
        p = self.perception
        if p.chat_idle(frame):
            return "closed"
        if any(r.applies(visible) for r in self.rules):
            return None
        if any(visible.get(n) for n in p.safe_open) or any(r.applies(visible) for r in self.order_rules):
            return "options"  # the butler's menu: harmless to leave open
        return None

    def _stalled(self, frame: Frame, visible: Visible) -> None:
        path = self.ctx.log.save_frame(frame, "unrecognised_dialogue")
        raise UnsafeState(
            f"unrecognised dialogue (labels seen: {sorted(visible) or 'none'}); screenshot: {path}. "
            "Calibrate its text and add a rule for it, or handle it manually."
        )

    def _settle(self, s: Snapshot) -> None:
        try:
            self._run_conversation(self.rules, self._settled, self._stalled)
        except RuleFailed as exc:
            self._rule_failed(exc)

    def _order(self, s: Snapshot) -> None:
        if not self.refill.can_order():
            return
        self.ctx.log.event("order_start", planks=s.planks)
        try:
            self._open_butler_dialogue(s)
            self._run_conversation(self.rules + self.order_rules, self._order_done, self._order_stalled)
        except RuleFailed as exc:
            self._rule_failed(exc)
            return
        except RecoverableError:
            if self.refill.trip is None:
                self.refill.order_failed()
            raise
        # `_order_done` only reports "departed" once an order was submitted.
        self.refill.update(self.ctx.now(), butler_visible=False, chat_idle=True, departure_timeout=0)
        self.mark_progress()
        self.ctx.log.event("butler_departed")

    def _order_done(self, frame: Frame, visible: Visible) -> str | None:
        p = self.perception
        if self.refill.trip is not None and p.chat_idle(frame) and p.butler_pick(frame).blob is None:
            return "departed"
        return None

    def _order_stalled(self, frame: Frame, visible: Visible) -> None:
        if self.perception.chat_idle(frame):
            if self.refill.trip is None:
                raise RecoverableError("the butler conversation ended without an order")
            raise RecoverableError("the butler did not leave after the order")
        self._stalled(frame, visible)

    def _collect(self, s: Snapshot) -> None:
        before = s.planks
        self.ctx.log.event("collect_start", planks=before, held=self.refill.trip.outstanding if self.refill.trip else 0)
        try:
            how = self._open_butler_dialogue(s)
            self._run_conversation(self.rules, self._settled, self._stalled)
            got = self._planks_now() - before
            if got <= 0 and how != "talked":
                # We joined a conversation we did not start (e.g. opened by Call Butler);
                # ask him directly once before concluding he holds nothing.
                pick = self.perception.butler_pick(self.ctx.grab())
                if pick.blob is not None:
                    self._talk_to_butler(pick.blob, restart=True)
                    self._run_conversation(self.rules, self._settled, self._stalled)
                    got = self._planks_now() - before
        except RuleFailed as exc:
            self._rule_failed(exc)
            return
        self.counts["collections"] += 1
        if got <= 0:
            self.ctx.log.event("butler_had_nothing_more")
            self.refill.nothing_more_held(self.ctx.now())
        self.mark_progress()

    def _planks_now(self) -> int:
        return self.perception.scan_inventory(self.ctx.grab()).count(PLANK)

    def _open_butler_dialogue(self, s: Snapshot) -> str:
        """Make sure a conversation with the butler is open; returns how it was opened."""
        ctx, p = self.ctx, self.perception
        if not s.chat_idle:
            return "existing"  # a recognised butler dialogue is already open
        pick = s.butler
        if pick.blob is None:
            if pick.ambiguous:
                raise RecoverableError("several highlights have the butler's colour")
            self._summon()
            arrival = ctx.wait_until(
                lambda f: "dialogue" if not p.chat_idle(f) else ("visible" if p.butler_pick(f).blob else None),
                self.settings.butler.arrival_timeout_s,
                "the butler to arrive",
            )
            if arrival == "dialogue":
                return "summoned"  # this server opens his dialogue automatically
            pick = p.butler_pick(ctx.grab())
            if pick.blob is None:
                raise RecoverableError("the butler disappeared again")
        self._talk_to_butler(pick.blob)
        return "talked"

    def _talk_to_butler(self, blob: Blob, *, restart: bool = False) -> None:
        """Click the butler and wait for his dialogue (to open, or to restart)."""
        ctx, p = self.ctx, self.perception
        chat_before = ctx.grab(p.chatbox)
        point = blob.pick_point(ctx.rng)
        if self.settings.butler.talk_mode == "menu":
            choose_option(
                ctx, p.menu, point, self._template("menu_talk_to_butler"), timeout=self.settings.timing.menu_timeout_s
            )
        else:
            ctx.input.move_to(point)
            uptext = p.layout.region("uptext")
            if uptext is not None and self.ctx.profile.templates.has("uptext_talk_to_butler"):
                ctx.wait_until(
                    lambda f: p.visible(f, "uptext_talk_to_butler", uptext),
                    0.8,
                    "mouse-over text 'Talk-to Demon butler'",
                )
            else:
                current = p.butler_pick(ctx.grab()).blob
                if current is None or not current.contains(point):
                    raise RecoverableError("the butler moved away from the cursor")
            ctx.input.click()
        timeout = self.settings.timing.dialogue_open_timeout_s
        if restart:
            ctx.wait_until(
                lambda f: _changed(chat_before, f), timeout, "the butler's dialogue to restart", region=p.chatbox
            )
        else:
            ctx.wait_until(lambda f: not p.chat_idle(f), timeout, "the butler's dialogue")

    def _summon(self) -> None:
        now = self.ctx.now()
        if not self.summon_steps:
            raise UnsafeState("the butler is not visible and butler.summon_method is 'none'")
        if self.refill.summon_in_progress(now):
            return  # an earlier summon is still under way: just wait for him
        self.ctx.log.event("summon_butler", method=self.settings.butler.summon_method)
        p = self.perception
        run_steps(
            self.ctx,
            self.summon_steps,
            self.ctx.profile.templates,
            menu=p.menu,
            color=self.ctx.profile.color,
            viewport=p.viewport,
            area=p.area,
        )
        self.refill.summoned_at = now
        self.counts["summons"] += 1
        if self.refill.trip is not None:
            self.refill.trip.summons += 1

    def _chase_butler(self, s: Snapshot) -> None:
        trip = self.refill.trip
        assert trip is not None
        now = self.ctx.now()
        if s.butler.blob is not None:
            self.refill.mark_returned(now)
            return
        if not self.summon_steps or trip.summons >= self.settings.butler.max_summons_per_trip:
            self.refill.disable(f"the butler did not come back {now - trip.submitted_at:.0f}s after the order")
            self.ctx.log.event("trip_abandoned", planks_left_with_butler=trip.outstanding)
            self.refill.trip = None
            return
        self.ctx.log.event("butler_overdue", seconds=round(now - trip.submitted_at, 1))
        self._summon()
        p = self.perception
        arrived = self.ctx.wait_until(
            lambda f: (not p.chat_idle(f)) or p.butler_pick(f).blob is not None,
            self.settings.butler.arrival_timeout_s,
            "the butler to arrive",
        )
        if arrived:
            self.refill.mark_returned(self.ctx.now())

    # -- recovery ------------------------------------------------------------------------
    def _client_problem(self, s: Snapshot) -> None:
        waited = self._since_first("client")
        problem = s.client.problem or "client problem"
        if waited == 0:
            self.ctx.log.event("client_problem", problem=problem)
        if waited < self.settings.safety.disconnect_grace_s:
            self.ctx.sleep(1.0)  # send nothing while the client may be reconnecting
            return
        raise UnsafeState(f"{problem} (for {waited:.0f}s)")

    def _close_stray_menu(self, s: Snapshot) -> None:
        assert s.menu is not None
        dismiss_menu(self.ctx, self.perception.menu, s.menu)
        self.record_failure("closed a right-click menu that was left open")

    def _close_stray_furniture_menu(self, s: Snapshot) -> None:
        self._close_furniture_menu()
        self.record_failure("closed a Furniture Creation Menu that was left open")

    def _close_furniture_menu(self) -> None:
        p = self.perception
        if self.ctx.profile.templates.has("furniture_close"):
            click_template(self.ctx, self._template("furniture_close"), p.viewport)
        else:
            self.ctx.input.press_key(self.settings.construction.close_interface_key)
        self.ctx.wait_until(lambda f: not p.furniture_open(f), 2.0, "the Furniture Creation Menu to close")

    def _reopen_inventory(self, s: Snapshot) -> None:
        if not self.ctx.profile.templates.has("tab_inventory"):
            raise UnsafeState("the inventory is not visible (open the inventory tab)")
        click_template(self.ctx, self._template("tab_inventory"), None)
        p = self.perception
        self.ctx.wait_until(lambda f: p.inventory_visible(f, p.scan_inventory(f)), 2.0, "the inventory to be visible")
        self.record_failure("reopened the inventory tab")

    def _unrecognised_dialogue(self, s: Snapshot) -> None:
        if self._since_first("dialogue") < self.settings.safety.unrecognised_grace_s:
            self.ctx.sleep(self.settings.timing.wait_poll_s)
            return
        if s.dialogue is Dialogue.ORDER:
            raise UnsafeState("the butler's order dialogue is open although a trip is still pending")
        self._stalled(s.frame, s.visible)

    def _table_missing(self, s: Snapshot) -> None:
        if self._since_first("table") < self.settings.safety.unrecognised_grace_s:
            self.ctx.sleep(self.settings.timing.wait_poll_s)
            return
        path = self.ctx.log.save_frame(s.frame, "table_not_found")
        if s.hotspot.ambiguous or (s.built is not None and s.built.ambiguous):
            raise UnsafeState(
                f"{s.hotspot.candidates} highlights share the Table space colour; refusing to guess ({path})"
            )
        raise UnsafeState(
            "cannot find the Table space or the Mahogany table: is building mode on, is the table on screen and "
            f"are the Object Markers visible? ({path})"
        )

    # -- reporting -----------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        c = self.counts
        trips = self.refill.history
        timed = [t.trip_s for t in trips if t.trip_s is not None]
        return {
            "tables_built": c["tables_built"],
            "tables_removed": c["tables_removed"],
            "xp_estimated": c["tables_built"] * XP_PER_TABLE,
            "butler_orders": c["orders"],
            "deliveries": c["deliveries"],
            "planks_delivered": c["planks_delivered"],
            "collections_from_butler": c["collections"],
            "butler_summons": c["summons"],
            "wages_paid": c["wages_paid"],
            "refills": "on" if self.refill.enabled else f"off: {self.refill.disabled_reason}",
            "runtime_s": round(self.ctx.now() - self._started, 1),
            # Observed mechanics, to compare with the standard OSRS values.
            "observed_planks_per_build": dict(self.build_costs) or "n/a",
            "observed_trip_s_avg": round(sum(timed) / len(timed), 1) if timed else "n/a",
            "observed_orders_between_wages": _gaps(self.orders_at_wage) or "n/a",
            "observed_max_delivery_per_trip": max((t.received for t in trips), default="n/a"),
        }

    def diagnose(self, frame: Frame) -> tuple[list[str], np.ndarray | None]:
        try:
            p = self.perception
        except Exception as exc:  # calibration too incomplete to observe anything
            return [f"cannot analyse the screen yet: {exc}"], None
        snap = p.observe(frame)
        lines = [
            f"client: {'ok' if snap.client.ok else snap.client.problem} (offset {snap.client.dx},{snap.client.dy})"
        ]
        if snap.client.ok:
            table = p.table_state(snap.hotspot, snap.built)
            lines += [
                f"table: {table.value} (table-space candidates: {snap.hotspot.candidates}, "
                f"built-table candidates: {'n/a' if snap.built is None else snap.built.candidates})",
                f"right-click menu: {snap.menu or 'none'}; furniture menu open: {snap.furniture_open}",
                f"chatbox idle: {snap.chat_idle}; dialogue: {snap.dialogue.value}; labels: {sorted(snap.visible)}",
                f"butler visible: {snap.butler.blob is not None} (candidates {snap.butler.candidates})",
                f"inventory visible: {snap.inventory_open}; planks: {snap.planks}; "
                f"free slots: {self._free_slots(snap)}; slots: {snap.inventory.summary() if snap.inventory else '-'}",
                f"construction xp drop visible: {snap.xp_drop}",
            ]
        return lines, p.annotate(snap)

    def hover_targets(self, frame: Frame) -> list[tuple[str, Point]]:
        snap = self.perception.observe(frame)
        targets = []
        if snap.hotspot.blob is not None:
            targets.append(("table space", snap.hotspot.blob.pick_point(self.ctx.rng)))
        if snap.built is not None and snap.built.blob is not None:
            targets.append(("mahogany table", snap.built.blob.pick_point(self.ctx.rng)))
        if snap.butler.blob is not None:
            targets.append(("demon butler", snap.butler.blob.pick_point(self.ctx.rng)))
        return targets

    @classmethod
    def simulation(cls, scenario: str, seed: int) -> Any:
        from .sim import build_simulation

        return build_simulation(scenario, seed)


def _changed(before: Frame, after: Frame) -> bool:
    if before.image.shape != after.image.shape:
        return True
    diff = np.abs(before.image.astype(np.int16) - after.image.astype(np.int16)).max(axis=2)
    return float((diff > 40).mean()) > 0.02


def _gaps(marks: list[int]) -> list[int]:
    previous = 0
    gaps = []
    for mark in marks:
        gaps.append(mark - previous)
        previous = mark
    return gaps
