"""A simulated Player-Owned House for dry runs and tests.

It renders a simplified client (game view with Object-Marker/NPC-Indicator
style outlines, classic right-click menus, chatbox dialogues, inventory, side
tabs) and implements the game behaviour this automation depends on:
furniture building/removal with confirmations, the Demon Butler's dialogue,
bank trips, partial deliveries when the inventory is full, wages every N
services, Call Butler / bell-pull, plus failure injection (disconnects,
unexpected dialogues, camera shifts, building mode off, low level, the user
grabbing the mouse).

The automation runs unchanged against it: real template matching on rendered
pixels, real colour detection, real guarded input — only the clock is virtual.
The calibration profile is produced the way a user would: by cropping labels
out of rendered screenshots.
"""

from __future__ import annotations

import heapq
import json
import random
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from autokit.geometry import Point, Rect
from autokit.runtime.profile import Profile
from autokit.testing.sim import SimulationBundle
from autokit.vision.color import ColorSpec
from osrskit.testing import widgets as w

CLIENT = Rect(60, 40, 765, 503)


def _c(x: int, y: int, width: int = 0, height: int = 0) -> Rect:
    return Rect(CLIENT.x + x, CLIENT.y + y, width, height)


VIEWPORT = _c(4, 4, 512, 334)
CHATBOX = _c(7, 345, 506, 128)
MINIMAP = _c(550, 4, 200, 156)
ANCHOR = _c(556, 10, 40, 40)
INV_TAB = _c(626, 168, 33, 36)
SETTINGS_TAB = _c(692, 168, 33, 36)
SIDE_PANEL = _c(553, 205, 190, 262)
INV_GRID = _c(563, 213, 162, 248)
UPTEXT = _c(8, 8, 240, 14)
HOUSE_OPTIONS_BTN = _c(578, 300, 140, 24)
CALL_BUTLER_BTN = _c(578, 336, 140, 24)
FURNITURE = _c(36, 24, 448, 296)
FURNITURE_CLOSE = Rect(FURNITURE.right - 24, FURNITURE.y + 5, 18, 18)

HOTSPOT_RGB = (0, 255, 255)
BUILT_RGB = (255, 0, 255)
BUTLER_RGB = (255, 255, 0)
BELL_RGB = (0, 255, 0)
NAME_OK = (255, 152, 31)
NAME_BAD = (200, 40, 40)
PLAYER = "Builder"
PLANKS_PER_TABLE = 6
MAX_CARRY = 26

TABLE_SHAPE = np.array([(-48, -18), (48, -18), (60, 22), (-60, 22)], np.int32)
CHAIR_SHAPE = np.array([(-14, -14), (14, -14), (16, 16), (-16, 16)], np.int32)
FURNITURE_ITEMS = [
    ("Wooden table", 10, "4 x Plank"),
    ("Oak table", 22, "4 x Oak plank"),
    ("Carved oak table", 31, "6 x Oak plank"),
    ("Teak table", 38, "4 x Teak plank"),
    ("Carved teak table", 45, "6 x Teak plank"),
    ("Mahogany table", 52, "6 x Mahogany plank"),
    ("Opulent table", 72, "Mahogany planks, gold leaf"),
]
RETURN_LINE = "Master, I have returned with what thou asked me to retrieve."
HOLD_PREFIX = "I shall wait with these"


@dataclass
class Scenario:
    """Server behaviour and injected events for one simulated run."""

    planks: int = 24
    other_items: tuple[str, ...] = ("hammer", "saw", "coins")
    coins: int = 1_000_000
    level: int = 60
    butler_present: bool = True
    call_opens_dialogue: bool = True  # the user's server: Call Butler opens his dialogue
    repeat_option: bool = True
    wage_every: int = 8
    wage: int = 10_000
    trip_s: float = 7.2
    build_s: float = 1.8
    remove_s: float = 1.2
    build_mode: bool = True
    camera_shift_px: int = 0
    disconnect_at: float | None = None
    reconnect_after: float | None = None
    unknown_dialogue_at: float | None = None
    user_mouse_at: float | None = None
    max_tables: int = 30
    summon: str = "house_options"
    with_empty_slot_model: bool = True
    with_built_marker: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "default": Scenario(),
    "butler-absent": Scenario(butler_present=False, max_tables=12),
    "bell-pull": Scenario(butler_present=False, summon="bell_pull", call_opens_dialogue=False, max_tables=8),
    "unpaid-wages": Scenario(coins=0, wage_every=2, max_tables=40),
    "camera": Scenario(camera_shift_px=45, max_tables=16),
    "reconnect": Scenario(disconnect_at=25.0, reconnect_after=5.0, max_tables=12),
    "disconnect": Scenario(disconnect_at=25.0, max_tables=40),
    "unknown-dialogue": Scenario(unknown_dialogue_at=30.0, max_tables=40),
    "no-build-mode": Scenario(build_mode=False),
    "low-level": Scenario(level=40),
}


@dataclass
class Dialog:
    kind: str  # "options" | "message" | "prompt"
    title: str
    options: list[tuple[str, Callable[[], None]]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    on_continue: Callable[[], None] | None = None
    on_submit: Callable[[str], None] | None = None
    typed: str = ""
    tag: str = ""


@dataclass
class Menu:
    rect: Rect
    entries: list[tuple[str, str, tuple[int, int, int], Callable[[], None] | None]]


class PohWorld:
    width = 1000
    height = 720

    def __init__(self, scenario: Scenario, seed: int = 1) -> None:
        self.sc = scenario
        self.rng = random.Random(seed)
        self.t = 0.0
        self.version = 0
        self.cursor = (900, 690)
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self.camera = (0, 0)
        self.table_built = False
        self.inventory: list[str | None] = [None] * 28
        for i, item in enumerate(scenario.other_items):
            self.inventory[i] = item
        self._add_planks(scenario.planks)
        self.coins = scenario.coins
        self.menu: Menu | None = None
        self.furniture = False
        self.dialog: Dialog | None = None
        self.deferred: list[Callable[[], None]] = []
        self.side_tab = "inventory"
        self.butler_state = "present" if scenario.butler_present else "absent"
        self.butler_offset = (72, 52)
        self.butler_held = 0
        self.butler_services = 0
        self.wage_due = False
        self.last_order: int | None = None
        self.connected = True
        self.xp_until = -1.0
        self.busy_until = 0.0
        self.stats: Counter[str] = Counter()
        self.wrong: list[str] = []
        self.history = ["Welcome to your house."]
        self.drawn: dict[str, Rect] = {}
        self._floor = self._make_floor()
        self._panel = self._make_panel()
        self.hooks: list[tuple[float, Callable[[], None]]] = []
        if scenario.disconnect_at is not None:
            self.at(scenario.disconnect_at, self._disconnect)
        if scenario.unknown_dialogue_at is not None:
            self.at(scenario.unknown_dialogue_at, self._unknown_dialogue)
        if scenario.user_mouse_at is not None:
            self.at(scenario.user_mouse_at, self._user_moves_mouse)

    # -- time ------------------------------------------------------------------------
    def at(self, t: float, fn: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (t, self._seq, fn))
        self._seq += 1

    def after(self, dt: float, fn: Callable[[], None]) -> None:
        self.at(self.t + dt, fn)

    def advance_to(self, t: float) -> None:
        while self._queue and self._queue[0][0] <= t:
            when, _, fn = heapq.heappop(self._queue)
            self.t = when
            fn()
            self.version += 1
        self.t = t

    # -- inventory ---------------------------------------------------------------------
    def planks(self) -> int:
        return self.inventory.count("mahogany_plank")

    def free_slots(self) -> int:
        return self.inventory.count(None)

    def _add_planks(self, n: int) -> int:
        added = 0
        for i, slot in enumerate(self.inventory):
            if added == n:
                break
            if slot is None:
                self.inventory[i] = "mahogany_plank"
                added += 1
        return added

    def _remove_planks(self, n: int) -> None:
        for i in range(27, -1, -1):
            if n == 0:
                return
            if self.inventory[i] == "mahogany_plank":
                self.inventory[i] = None
                n -= 1

    # -- geometry ------------------------------------------------------------------------
    def table_center(self) -> tuple[int, int]:
        return VIEWPORT.x + 256 + self.camera[0], VIEWPORT.y + 160 + self.camera[1]

    def table_poly(self) -> np.ndarray:
        return TABLE_SHAPE + np.array(self.table_center(), np.int32)

    def chair_polys(self) -> list[np.ndarray]:
        cx, cy = self.table_center()
        return [CHAIR_SHAPE + np.array((cx + dx, cy + 4), np.int32) for dx in (-108, 108)]

    def player_rect(self) -> Rect:
        cx, cy = self.table_center()
        return Rect(cx - 10, cy + 42, 20, 44)

    def butler_rect(self) -> Rect:
        cx, cy = self.table_center()
        return Rect(cx + self.butler_offset[0] - 11, cy + self.butler_offset[1] - 23, 22, 46)

    def bell_rect(self) -> Rect:
        return Rect(VIEWPORT.x + 450 + self.camera[0], VIEWPORT.y + 26 + self.camera[1] // 3, 12, 40)

    @staticmethod
    def _inside(poly: np.ndarray, p: tuple[int, int]) -> bool:
        return cv2.pointPolygonTest(poly.reshape(-1, 1, 2), (float(p[0]), float(p[1])), False) >= 0

    def objects_at(self, p: tuple[int, int]) -> list[tuple[str, str, tuple[int, int, int], Callable[[], None] | None]]:
        point = Point(*p)
        entries: list[tuple[str, str, tuple[int, int, int], Callable[[], None] | None]] = []
        if self.butler_state == "present" and self.butler_rect().contains(point):
            entries += [
                ("Talk-to", "Demon butler", w.NPC_YELLOW, self._talk),
                ("Examine", "Demon butler", w.NPC_YELLOW, self._wrong_action("examine butler")),
            ]
        if self._inside(self.table_poly(), p) and (self.build_mode_on or self.table_built):
            if self.table_built:
                entries.append(("Examine", "Mahogany table", w.OBJECT_CYAN, self._wrong_action("examine table")))
                if self.build_mode_on:
                    entries.append(("Remove", "Mahogany table", w.OBJECT_CYAN, self._remove_selected))
            else:
                entries += [
                    ("Examine", "Table space", w.OBJECT_CYAN, self._wrong_action("examine table space")),
                    ("Build", "Table space", w.OBJECT_CYAN, self._build_selected),
                ]
        if self.build_mode_on:
            for poly in self.chair_polys():
                if self._inside(poly, p):
                    entries += [
                        ("Examine", "Chair space", w.OBJECT_CYAN, self._wrong_action("examine chair space")),
                        ("Build", "Chair space", w.OBJECT_CYAN, self._wrong_action("build chair space")),
                    ]
        if self.bell_rect().contains(point):
            entries += [
                ("Ring", "Bell-pull", w.OBJECT_CYAN, self._ring_bell),
                ("Examine", "Bell-pull", w.OBJECT_CYAN, self._wrong_action("examine bell-pull")),
            ]
        return entries

    @property
    def build_mode_on(self) -> bool:
        return self.sc.build_mode

    # -- input -------------------------------------------------------------------------
    def on_move(self, x: int, y: int) -> None:
        self.cursor = (x, y)
        if self.menu is not None and not self.menu.rect.expand(10).contains(Point(x, y)):
            self.menu = None
        self.version += 1

    def on_button(self, button: str, down: bool) -> None:
        if not down:
            return
        self.version += 1
        if not self.connected:
            self.stats["inputs_while_disconnected"] += 1
            return
        p = Point(*self.cursor)
        if button == "right":
            self.menu = None
            if VIEWPORT.contains(p) and not (self.furniture and FURNITURE.contains(p)):
                self._open_menu(self.cursor)
            return
        if self.menu is not None:
            menu, self.menu = self.menu, None
            if menu.rect.contains(p):
                index = int((p.y - menu.rect.y - w.MENU_HEADER_H) // w.MENU_ROW_H)
                if 0 <= index < len(menu.entries):
                    action, target, _, callback = menu.entries[index]
                    self.stats[f"menu:{action} {target}".strip()] += 1
                    if callback is not None:
                        callback()
            return
        if self.furniture and FURNITURE.contains(p):
            self._furniture_click(p)
            return
        if self.dialog is not None and CHATBOX.contains(p):
            self._dialog_click(p)
            return
        if INV_TAB.contains(p):
            self.side_tab = "inventory"
            return
        if SETTINGS_TAB.contains(p):
            self.side_tab = "settings"
            return
        if self.side_tab == "settings" and HOUSE_OPTIONS_BTN.contains(p):
            self.side_tab = "house"
            return
        if self.side_tab == "house" and CALL_BUTLER_BTN.contains(p):
            self._call_butler()
            return
        if VIEWPORT.contains(p):
            if self.furniture:
                self.furniture = False
            if self.butler_state == "present" and self.butler_rect().contains(p):
                self._talk()
            else:
                self._wrong_action("walk here")()

    def on_key(self, key: str, down: bool) -> None:
        if not down:
            return
        self.version += 1
        if not self.connected:
            self.stats["inputs_while_disconnected"] += 1
            return
        d = self.dialog
        if d is not None:
            if d.tag == "unknown":
                self.stats["keys_into_unknown_dialogue"] += 1
            if d.kind == "prompt":
                if key.isdigit():
                    d.typed += key
                elif key == "backspace":
                    d.typed = d.typed[:-1]
                elif key == "enter" and d.on_submit is not None:
                    d.on_submit(d.typed)
                else:
                    self.stats["stray_keys"] += 1
            elif d.kind == "options" and key.isdigit() and 1 <= int(key) <= len(d.options):
                self._choose(d, int(key) - 1)
            elif d.kind == "message" and key == "space" and d.on_continue is not None:
                d.on_continue()
            else:
                self.stats["stray_keys"] += 1
            return
        if self.furniture and key == "escape":
            self.furniture = False
            return
        self.stats["stray_keys"] += 1

    # -- world actions -------------------------------------------------------------------
    def _open_menu(self, at: tuple[int, int]) -> None:
        entries = [("Walk here", "", w.WHITE, self._wrong_action("walk here"))]
        entries += self.objects_at(at)
        entries.append(("Cancel", "", w.WHITE, None))
        mw, mh = w.menu_size([(a, t, c) for a, t, c, _ in entries])
        x = min(max(at[0] - mw // 2, CLIENT.x), CLIENT.right - mw)
        y = min(max(at[1] - 8, CLIENT.y), CLIENT.bottom - mh)
        self.menu = Menu(Rect(x, y, mw, mh), entries)

    def _wrong_action(self, what: str) -> Callable[[], None]:
        def act() -> None:
            self.wrong.append(f"{self.t:.1f}s {what}")
            self._close_dialog()

        return act

    def _build_selected(self) -> None:
        self._close_dialog()
        self.after(0.3, self._open_furniture)

    def _open_furniture(self) -> None:
        if self.build_mode_on and not self.table_built:
            self.furniture = True

    def _furniture_layout(self) -> list[tuple[str, int, str, Rect]]:
        layout = []
        for i, (name, level, mats) in enumerate(FURNITURE_ITEMS):
            col, row = i % 2, i // 2
            layout.append((name, level, mats, Rect(FURNITURE.x + 10 + col * 218, FURNITURE.y + 32 + row * 64, 210, 58)))
        return layout

    def _available(self, name: str, level: int) -> bool:
        if self.sc.level < level:
            return False
        return name == "Mahogany table" and self.planks() >= PLANKS_PER_TABLE

    def _furniture_click(self, p: Point) -> None:
        if FURNITURE_CLOSE.contains(p):
            self.furniture = False
            return
        for name, level, _, rect in self._furniture_layout():
            if rect.contains(p):
                if name != "Mahogany table":
                    self.stats["wrong_furniture_clicks"] += 1
                    self.wrong.append(f"{self.t:.1f}s clicked {name}")
                    return
                if not self._available(name, level) or self.t < self.busy_until:
                    self.stats["unavailable_clicks"] += 1
                    return
                self.furniture = False
                self.busy_until = self.t + self.sc.build_s
                self.after(self.sc.build_s, self._build_complete)
                return

    def _build_complete(self) -> None:
        self.table_built = True
        self._remove_planks(PLANKS_PER_TABLE)
        self.stats["tables_built"] += 1
        self.xp_until = self.t + 1.2
        self.after(1.2, lambda: None)  # re-render when the XP drop disappears

    def _remove_selected(self) -> None:
        self._close_dialog()
        self.after(0.6, self._remove_confirm)

    def _remove_confirm(self) -> None:
        self.dialog = Dialog(
            "options", "Really remove it?", [("Yes", self._remove_yes), ("No", self._close_dialog)], tag="remove"
        )

    def _remove_yes(self) -> None:
        self._close_dialog()
        self.after(self.sc.remove_s, self._remove_complete)

    def _remove_complete(self) -> None:
        self.table_built = False
        self.stats["tables_removed"] += 1
        if self.sc.camera_shift_px:
            s = self.sc.camera_shift_px
            self.camera = (self.rng.randint(-s, s), self.rng.randint(-s // 2, s // 2))

    # -- dialogues -----------------------------------------------------------------------
    def _close_dialog(self) -> None:
        self.dialog = None
        if self.deferred:
            self.deferred.pop(0)()

    def _show(self, fn: Callable[[], None]) -> None:
        if self.dialog is None:
            fn()
        else:
            self.deferred.append(fn)

    def _choose(self, dialog: Dialog, index: int) -> None:
        text, callback = dialog.options[index]
        self.stats[f"option:{text}"] += 1
        self.dialog = None
        callback()
        if self.dialog is None and self.deferred:
            self.deferred.pop(0)()

    def _dialog_click(self, p: Point) -> None:
        d = self.dialog
        assert d is not None
        if d.tag == "unknown":
            self.stats["clicks_into_unknown_dialogue"] += 1
        if d.kind == "options":
            for i, rect in enumerate(w.chat_option_rects(CHATBOX, [t for t, _ in d.options])):
                if rect.expand(2).contains(p):
                    self._choose(d, i)
                    return
        elif d.kind == "message" and d.on_continue is not None:
            d.on_continue()

    def _unknown_dialogue(self) -> None:
        self.dialog = Dialog(
            "message",
            "Strange voice",
            lines=["Something unexpected whispers to you."],
            on_continue=self._close_dialog,
            tag="unknown",
        )

    # -- the butler -----------------------------------------------------------------------
    def _jitter_butler(self) -> None:
        self.butler_offset = (self.rng.randint(60, 90), self.rng.randint(40, 62))

    def _talk(self) -> None:
        self._close_dialog()
        self.after(0.3, self._open_butler_talk)

    def _open_butler_talk(self) -> None:
        if self.butler_state != "present":
            return
        if self.wage_due:
            self._wage_dialog()
        elif self.butler_held > 0:
            self._deliver_held()
        else:
            self._main_options()

    def _main_options(self) -> None:
        options: list[tuple[str, Callable[[], None]]] = []
        if self.last_order and self.sc.repeat_option:
            q = self.last_order
            options.append((f"Fetch from bank: {q} x Mahogany planks", lambda: self._place_order(q)))
        options += [
            ("Fetch from bank", self._item_list),
            ("Take to sawmill", self._wrong_action("sawmill option")),
            ("Misc...", self._wrong_action("misc option")),
            ("Nevermind", self._close_dialog),
        ]
        self.dialog = Dialog("options", "What is thy bidding, master?", options, tag="butler")

    def _item_list(self) -> None:
        self.dialog = Dialog(
            "options",
            "Fetch what, master?",
            [
                ("Oak planks", self._wrong_action("ordered oak planks")),
                ("Teak planks", self._wrong_action("ordered teak planks")),
                ("Mahogany planks", self._amount_prompt),
                ("More...", self._wrong_action("more items")),
            ],
            tag="butler",
        )

    def _amount_prompt(self) -> None:
        self.dialog = Dialog("prompt", "Enter amount:", on_submit=self._submit_amount, tag="butler")

    def _submit_amount(self, text: str) -> None:
        self.dialog = None
        quantity = int(text) if text else 0
        if not 1 <= quantity <= MAX_CARRY:
            self.history.append("That is more than I can carry, master.")
            return
        self._place_order(quantity)

    def _place_order(self, quantity: int) -> None:
        self.dialog = None
        if self.butler_held > 0:
            self.stats["orders_while_items_held"] += 1
        self.last_order = quantity
        self.stats["orders"] += 1

        def depart() -> None:
            self.butler_state = "away"
            self.butler_services += 1
            self.after(self.sc.trip_s, lambda: self._butler_returns(quantity))

        self.after(0.6, depart)

    def _butler_returns(self, quantity: int) -> None:
        self.butler_state = "present"
        self._jitter_butler()
        self.butler_held += quantity
        if self.sc.wage_every and self.butler_services % self.sc.wage_every == 0:
            self.wage_due = True
        self._show(self._open_butler_talk)

    def _deliver_held(self) -> None:
        n = self._add_planks(min(self.free_slots(), self.butler_held))
        self.butler_held -= n
        self.stats["planks_delivered"] += n
        lines = [RETURN_LINE]
        if self.butler_held:
            lines.append(f"{HOLD_PREFIX} {self.butler_held} items until thou art ready.")
        self.dialog = Dialog("message", "Demon butler", lines=lines, on_continue=self._close_dialog, tag="butler")

    def _wage_dialog(self) -> None:
        self.dialog = Dialog(
            "options",
            f"Thy wages are due: {self.sc.wage:,} coins.",
            [
                (f"Pay {self.sc.wage:,} coins", self._pay_wage),
                ("Not now", self._close_dialog),
            ],
            tag="butler",
        )

    def _pay_wage(self) -> None:
        if self.coins >= self.sc.wage:
            self.coins -= self.sc.wage
            self.wage_due = False
            self.stats["wages_paid"] += 1
            self._deliver_held()
        else:
            self.stats["wage_attempts_without_coins"] += 1
            self.dialog = Dialog(
                "message",
                "Demon butler",
                lines=["Thou dost not have enough coins, master."],
                on_continue=self._close_dialog,
                tag="butler",
            )

    def _call_butler(self) -> None:
        self.stats["summons"] += 1
        if self.butler_state == "away":
            self.stats["summons_while_away"] += 1
            self.history.append("Your servant is busy.")
            return

        def arrive() -> None:
            self.butler_state = "present"
            self._jitter_butler()
            if self.sc.call_opens_dialogue:
                self._show(self._open_butler_talk)

        self.after(1.2 if self.butler_state == "absent" else 0.6, arrive)

    def _ring_bell(self) -> None:
        self._call_butler()

    # -- injected events -----------------------------------------------------------------
    def _disconnect(self) -> None:
        self.connected = False
        self.menu = None
        if self.sc.reconnect_after is not None:
            self.after(self.sc.reconnect_after, self._reconnect)

    def _reconnect(self) -> None:
        self.connected = True

    def _user_moves_mouse(self) -> None:
        x, y = self.cursor
        self.cursor = (min(self.width - 1, x + 160), min(self.height - 1, y + 120))

    # -- rendering -----------------------------------------------------------------------
    @staticmethod
    def _make_floor() -> np.ndarray:
        noise = np.random.default_rng(7).integers(-6, 7, (VIEWPORT.h, VIEWPORT.w, 3))
        floor = np.empty((VIEWPORT.h, VIEWPORT.w, 3), np.int16)
        floor[:] = w.bgr((100, 72, 48))
        floor[:64] = w.bgr((70, 60, 52))
        return np.clip(floor + noise, 0, 255).astype(np.uint8)

    @staticmethod
    def _make_panel() -> np.ndarray:
        noise = np.random.default_rng(3).integers(-4, 5, (SIDE_PANEL.h, SIDE_PANEL.w, 3))
        panel = np.empty((SIDE_PANEL.h, SIDE_PANEL.w, 3), np.int16)
        panel[:] = w.bgr(w.PANEL_BG)
        return np.clip(panel + noise, 0, 255).astype(np.uint8)

    def slot_rect(self, index: int) -> Rect:
        col, row = index % 4, index // 4
        return Rect(INV_GRID.x + col * 42, INV_GRID.y + row * 36, 36, 32)

    def render(self) -> np.ndarray:
        img = np.full((self.height, self.width, 3), 28, np.uint8)
        self.drawn = {}
        w.fill(img, CLIENT, (0, 0, 0))
        self._render_viewport(img)
        self._render_side(img)
        self._render_chat(img)
        if self.furniture:
            self._render_furniture(img)
        if self.t < self.xp_until:
            self.drawn["+840"] = w.draw_text(img, VIEWPORT.right - 70, VIEWPORT.y + 40, "+840", w.WHITE)
        if self.menu is not None:
            m = self.menu
            rows = w.draw_context_menu(img, m.rect.x, m.rect.y, [(a, t, c) for a, t, c, _ in m.entries])
            for (action, target, _, _), (_, label) in zip(m.entries, rows, strict=True):
                self.drawn[f"{action} {target}".strip()] = label
        if not self.connected:
            box = Rect(VIEWPORT.x + 140, VIEWPORT.y + 130, 232, 44)
            w.fill(img, box, (0, 0, 0))
            w.outline(img, box, w.WHITE)
            self.drawn["Connection lost"] = w.draw_text(img, box.x + 70, box.y + 8, "Connection lost", w.WHITE)
            w.draw_text(img, box.x + 8, box.y + 24, "Please wait - attempting to reestablish", w.WHITE)
        return img

    def _render_viewport(self, img: np.ndarray) -> None:
        vp = self._floor.copy()
        ox, oy = VIEWPORT.x, VIEWPORT.y

        def local(poly: np.ndarray) -> np.ndarray:
            return (poly - np.array((ox, oy), np.int32)).reshape(-1, 1, 2)

        bell = self.bell_rect().translate(-ox, -oy)
        cv2.rectangle(vp, (bell.x, bell.y), (bell.right - 1, bell.bottom - 1), w.bgr((150, 40, 40)), -1)
        cv2.rectangle(vp, (bell.x - 2, bell.y - 2), (bell.right + 1, bell.bottom + 1), w.bgr(BELL_RGB), 2)
        for poly in self.chair_polys():
            cv2.fillPoly(vp, [local(poly)], w.bgr((122, 98, 76)))
        table = local(self.table_poly())
        if self.table_built:
            cv2.fillPoly(vp, [table], w.bgr((96, 40, 28)))
            if self.sc.with_built_marker:
                cv2.polylines(vp, [table], True, w.bgr(BUILT_RGB), 2)
        elif self.build_mode_on:
            cv2.polylines(vp, [table], True, w.bgr((170, 170, 170)), 1)
            cv2.polylines(vp, [table], True, w.bgr(HOTSPOT_RGB), 2)
        player = self.player_rect().translate(-ox, -oy)
        cv2.rectangle(vp, (player.x, player.y), (player.right - 1, player.bottom - 1), w.bgr((60, 60, 140)), -1)
        if self.butler_state == "present":
            b = self.butler_rect().translate(-ox, -oy)
            cv2.rectangle(vp, (b.x, b.y), (b.right - 1, b.bottom - 1), w.bgr((48, 20, 20)), -1)
            cv2.rectangle(vp, (b.x - 2, b.y - 2), (b.right + 1, b.bottom + 1), w.bgr(BUTLER_RGB), 2)
        img[VIEWPORT.y : VIEWPORT.bottom, VIEWPORT.x : VIEWPORT.right] = vp
        cursor = Point(*self.cursor)
        if self.menu is None and VIEWPORT.contains(cursor) and not (self.furniture and FURNITURE.contains(cursor)):
            objects = self.objects_at(self.cursor)
            if objects:
                action, target, color, _ = objects[0]
                parts = [(action, w.WHITE), (target, color)]
            else:
                parts = [("Walk here", w.WHITE)]
            self.drawn["uptext"] = w.draw_label(img, UPTEXT.x, UPTEXT.y, parts)

    def _render_side(self, img: np.ndarray) -> None:
        w.fill(img, MINIMAP, (20, 30, 20))
        cv2.circle(img, ANCHOR.center.rounded(), 18, w.bgr((45, 45, 45)), -1)
        cv2.circle(img, ANCHOR.center.rounded(), 18, w.bgr((220, 220, 220)), 2)
        cx, cy = ANCHOR.center.rounded()
        cv2.fillPoly(img, [np.array([(cx, cy - 14), (cx - 5, cy), (cx + 5, cy)], np.int32)], w.bgr((210, 30, 30)))
        cv2.fillPoly(img, [np.array([(cx, cy + 14), (cx - 5, cy), (cx + 5, cy)], np.int32)], w.bgr((230, 230, 230)))
        for tab, name in ((INV_TAB, "tab_inventory"), (SETTINGS_TAB, "tab_settings")):
            selected = (name == "tab_inventory" and self.side_tab == "inventory") or (
                name == "tab_settings" and self.side_tab in ("settings", "house")
            )
            w.fill(img, tab, (110, 30, 22) if selected else (66, 56, 44))
            icon = Rect(tab.x + 7, tab.y + 8, 20, 20)
            if name == "tab_inventory":
                w.fill(img, icon, (150, 110, 60))
                w.outline(img, icon, (40, 25, 10))
                w.fill(img, Rect(icon.x + 6, icon.y + 3, 8, 4), (90, 60, 30))
            else:
                w.fill(img, icon, (120, 120, 130))
                w.outline(img, icon, (20, 20, 20))
                cv2.line(img, (icon.x + 4, icon.y + 4), (icon.right - 5, icon.bottom - 5), w.bgr((30, 30, 30)), 2)
            self.drawn[f"{name}_icon"] = icon
            self.drawn[f"{name}_full"] = tab
        img[SIDE_PANEL.y : SIDE_PANEL.bottom, SIDE_PANEL.x : SIDE_PANEL.right] = self._panel
        if self.side_tab == "inventory":
            for i, item in enumerate(self.inventory):
                if item is not None:
                    w.draw_item(img, self.slot_rect(i), item)
        elif self.side_tab == "settings":
            w.draw_text(img, SIDE_PANEL.x + 70, SIDE_PANEL.y + 10, "Settings", w.WHITE)
            self._button(img, HOUSE_OPTIONS_BTN, "House Options")
        else:
            w.draw_text(img, SIDE_PANEL.x + 52, SIDE_PANEL.y + 10, "House Options", w.WHITE)
            w.draw_text(
                img,
                SIDE_PANEL.x + 30,
                SIDE_PANEL.y + 60,
                f"Building mode: {'On' if self.build_mode_on else 'Off'}",
                w.WHITE,
            )
            self._button(img, CALL_BUTLER_BTN, "Call Butler")

    def _button(self, img: np.ndarray, rect: Rect, label: str) -> None:
        w.fill(img, rect, (84, 72, 58))
        w.outline(img, rect, (20, 16, 12))
        tw, th = w.text_size(label)
        w.draw_text(img, rect.x + (rect.w - tw) // 2, rect.y + (rect.h - th) // 2, label, (255, 152, 31))
        self.drawn[label] = rect

    def _render_chat(self, img: np.ndarray) -> None:
        d = self.dialog
        if d is None:
            self.drawn["chat_idle"] = w.draw_chat_idle(img, CHATBOX, PLAYER, self.history)
            return
        if d.kind == "options":
            texts = [t for t, _ in d.options]
            hover = None
            for i, rect in enumerate(w.chat_option_rects(CHATBOX, texts)):
                if rect.expand(2).contains(Point(*self.cursor)):
                    hover = i
            self.drawn.update(w.draw_chat_options(img, CHATBOX, d.title, texts, hover))
        elif d.kind == "message":
            self.drawn.update(w.draw_chat_message(img, CHATBOX, d.title, d.lines))
        else:
            self.drawn.update(w.draw_chat_prompt(img, CHATBOX, d.title, d.typed))

    def _render_furniture(self, img: np.ndarray) -> None:
        w.fill(img, FURNITURE, (76, 64, 50))
        w.outline(img, FURNITURE, (24, 18, 12), 2)
        title = "Furniture Creation Menu"
        tw, _ = w.text_size(title)
        self.drawn[title] = w.draw_text(img, FURNITURE.x + (FURNITURE.w - tw) // 2, FURNITURE.y + 10, title, NAME_OK)
        w.fill(img, FURNITURE_CLOSE, (120, 30, 20))
        w.draw_text(img, FURNITURE_CLOSE.x + 6, FURNITURE_CLOSE.y + 3, "X", w.WHITE)
        self.drawn["furniture_close"] = FURNITURE_CLOSE
        for name, level, mats, rect in self._furniture_layout():
            w.fill(img, rect, (60, 50, 38))
            w.outline(img, rect, (30, 24, 18))
            w.fill(img, Rect(rect.x + 6, rect.y + 9, 36, 40), (96, 40, 28) if "Mahogany" in name else (140, 110, 70))
            color = NAME_OK if self._available(name, level) else NAME_BAD
            self.drawn[name] = w.draw_text(img, rect.x + 50, rect.y + 6, name, color)
            w.draw_text(img, rect.x + 50, rect.y + 22, f"Level {level}", w.WHITE)
            w.draw_text(img, rect.x + 50, rect.y + 38, mats, (190, 190, 190))

    # -- ground truth ----------------------------------------------------------------------
    def report(self) -> dict[str, Any]:
        return {
            "tables_built": self.stats["tables_built"],
            "tables_removed": self.stats["tables_removed"],
            "orders": self.stats["orders"],
            "planks_delivered": self.stats["planks_delivered"],
            "wages_paid": self.stats["wages_paid"],
            "summons": self.stats["summons"],
            "summons_while_away": self.stats["summons_while_away"],
            "orders_while_items_held": self.stats["orders_while_items_held"],
            "wrong_furniture_clicks": self.stats["wrong_furniture_clicks"],
            "wrong_actions": list(self.wrong),
            "stray_keys": self.stats["stray_keys"],
            "keys_into_unknown_dialogue": self.stats["keys_into_unknown_dialogue"],
            "inputs_while_disconnected": self.stats["inputs_while_disconnected"],
            "planks_in_inventory": self.planks(),
            "planks_held_by_butler": self.butler_held,
            "sim_time_s": round(self.t, 1),
        }


# -- profile ("calibration") ------------------------------------------------------------------


def _crop(img: np.ndarray, rect: Rect) -> np.ndarray:
    return img[rect.y : rect.bottom, rect.x : rect.right].copy()


def build_profile(root: Path, scenario: Scenario) -> Profile:
    """Create a calibrated profile by cropping labels out of rendered screens."""
    templates_dir = root / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    world = PohWorld(replace(scenario, planks=24, level=60, build_mode=True, butler_present=True), seed=0)
    world.cursor = (990, 710)

    homes: dict[str, dict[str, list[int]]] = {}

    def save(name: str, rect: Rect, image: np.ndarray | None = None) -> None:
        cv2.imwrite(str(templates_dir / f"{name}.png"), _crop(image if image is not None else img, rect))
        homes[name] = {"rect": rect.to_list()}  # where it was captured, like the calibration wizard records

    img = world.render()
    d = dict(world.drawn)
    save("client_anchor", ANCHOR)
    save("chat_idle", d["chat_idle"].expand(1))
    save("inventory_open", d["tab_inventory_full"])
    save("tab_inventory", d["tab_inventory_icon"])
    save("tab_settings", d["tab_settings_icon"])
    plank_slot = world.inventory.index("mahogany_plank")
    save("item_mahogany_plank", world.slot_rect(plank_slot).expand(-2))
    if scenario.with_empty_slot_model:
        save("inventory_empty_slot", world.slot_rect(27).expand(-3))

    world._open_menu(world.table_center())
    img = world.render()
    save("menu_build_table_space", world.drawn["Build Table space"])
    world.menu = None
    world.furniture = True
    img = world.render()
    save("furniture_menu_title", world.drawn["Furniture Creation Menu"])
    save("furniture_mahogany_table", world.drawn["Mahogany table"])
    save("furniture_close", world.drawn["furniture_close"])
    world.furniture = False
    world.table_built = True
    world._open_menu(world.table_center())
    img = world.render()
    save("menu_remove_mahogany_table", world.drawn["Remove Mahogany table"])
    world.menu = None
    world._remove_confirm()
    img = world.render()
    save("remove_confirm", world.drawn["Really remove it?"])
    world.table_built = False

    world.last_order = 24
    world._main_options()
    img = world.render()
    save("butler_main_options", world.drawn["What is thy bidding, master?"])
    save("butler_opt_fetch", world.drawn["Fetch from bank"])
    if scenario.repeat_option:  # a server without the option gives the user nothing to capture
        save("butler_opt_repeat_order", world.drawn["Fetch from bank: 24 x Mahogany planks"])
    world._item_list()
    img = world.render()
    save("butler_opt_mahogany_planks", world.drawn["Mahogany planks"])
    save("butler_opt_more", world.drawn["More..."])
    world._amount_prompt()
    img = world.render()
    save("prompt_enter_amount", world.drawn["Enter amount:"])
    hold_line = f"{HOLD_PREFIX} 5 items until thou art ready."
    world.dialog = Dialog("message", "Demon butler", lines=[RETURN_LINE, hold_line])
    img = world.render()
    save("butler_returned_msg", world.drawn[RETURN_LINE])
    line = world.drawn[hold_line]
    save("butler_holding_msg", Rect(line.x, line.y, w.text_size(HOLD_PREFIX)[0], line.h))
    world._wage_dialog()
    img = world.render()
    save("butler_wage_request", world.drawn[f"Thy wages are due: {scenario.wage:,} coins."])
    save("butler_opt_pay_wage", world.drawn[f"Pay {scenario.wage:,} coins"])
    world.dialog = Dialog("message", "Demon butler", lines=["Thou dost not have enough coins, master."])
    img = world.render()
    save("butler_wage_insufficient", world.drawn["Thou dost not have enough coins, master."])
    world.dialog = None

    world.cursor = world.butler_rect().center.rounded()
    img = world.render()
    save("uptext_talk_to_butler", world.drawn["uptext"])
    world.cursor = (990, 710)
    world._open_menu(world.butler_rect().center.rounded())
    img = world.render()
    save("menu_talk_to_butler", world.drawn["Talk-to Demon butler"])
    world._open_menu(world.bell_rect().center.rounded())
    img = world.render()
    save("menu_ring_bell_pull", world.drawn["Ring Bell-pull"])
    world.menu = None
    world.side_tab = "settings"
    img = world.render()
    save("settings_house_options", world.drawn["House Options"])
    world.side_tab = "house"
    img = world.render()
    save("house_options_call_butler", world.drawn["Call Butler"])
    world.side_tab = "inventory"
    world.connected = False
    img = world.render()
    save("connection_lost", world.drawn["Connection lost"])
    world.connected = True
    world.xp_until = world.t + 10
    img = world.render()
    save("xp_drop_construction", world.drawn["+840"])

    colors = {
        "hotspot_empty": ColorSpec(HOTSPOT_RGB, 12).to_dict(),
        "butler": ColorSpec(BUTLER_RGB, 12).to_dict(),
        "bell_pull": ColorSpec(BELL_RGB, 12).to_dict(),
    }
    if scenario.with_built_marker:
        colors["table_built"] = ColorSpec(BUILT_RGB, 12).to_dict()
    calibration = {
        "anchor": {"template": "client_anchor", "rect": ANCHOR.to_list()},
        "regions": {
            "viewport": VIEWPORT.to_list(),
            "chatbox": CHATBOX.to_list(),
            "inventory_grid": INV_GRID.to_list(),
            "uptext": UPTEXT.to_list(),
        },
        "colors": colors,
        "templates": homes,
    }
    (root / "calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    settings: dict[str, Any] = {
        "run": {"max_tables": scenario.max_tables, "status_every_s": 60},
        "butler": {"summon_method": scenario.summon},
        "inventory": {"other_items": len(scenario.other_items)},
    }
    _merge(settings, scenario.settings)
    (root / "settings.yaml").write_text(yaml.safe_dump(settings), encoding="utf-8")
    from .automation import MahoganyTables

    return Profile.load(root, MahoganyTables.defaults_file())


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def build_simulation(name: str | Scenario, seed: int, root: Path | None = None) -> SimulationBundle:
    scenario = name if isinstance(name, Scenario) else SCENARIOS.get(name)
    if scenario is None:
        raise ValueError(f"unknown scenario '{name}'; available: {', '.join(SCENARIOS)}")
    profile_root = root or Path(tempfile.mkdtemp(prefix="poh-sim-"))
    profile = build_profile(profile_root, scenario)
    return SimulationBundle(PohWorld(scenario, seed), profile)
