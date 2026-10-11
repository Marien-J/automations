"""Turns one screenshot into a :class:`Snapshot` of everything the automation
needs to decide its next action. No input is ever sent from here."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import cv2
import numpy as np

from autokit.errors import ConfigError
from autokit.geometry import Point, Rect
from autokit.runtime.profile import Profile
from autokit.runtime.rules import Rule, Visible
from autokit.vision.color import ColorSpec
from autokit.vision.frame import Frame
from autokit.vision.templates import TemplateLibrary, find_all, find_best
from osrskit.client import ClientStatus, ClientWatch, Layout
from osrskit.context_menu import ContextMenu, MenuStyle
from osrskit.highlights import Highlight, Pick
from osrskit.inventory import EmptySlotModel, Inventory, InventoryScan

from .assets import ANCHOR
from .settings import Settings

PLANK = "plank"


class Table(enum.Enum):
    EMPTY = "empty"  # Table space visible, ready to build
    BUILT = "built"  # Mahogany table visible
    UNKNOWN = "unknown"  # neither (or both / ambiguous)


class Dialogue(enum.Enum):
    NONE = "none"  # chatbox idle
    SAFE_OPEN = "safe_open"  # known dialogue that may be left open
    REMOVE_CONFIRM = "remove_confirm"
    SETTLE = "settle"  # known butler/system dialogue with an applicable rule
    ORDER = "order"  # part of the butler's fetch conversation
    UNKNOWN = "unknown"  # a dialogue nothing recognises -> never act on it


@dataclass
class Snapshot:
    frame: Frame
    client: ClientStatus
    menu: Rect | None = None
    furniture_open: bool = False
    hotspot: Pick = field(default_factory=lambda: Pick(None, 0))
    built: Pick | None = None
    chat_idle: bool = True
    visible: Visible = field(default_factory=dict)
    dialogue: Dialogue = Dialogue.NONE
    butler: Pick = field(default_factory=lambda: Pick(None, 0))
    inventory: InventoryScan | None = None
    inventory_open: bool = True
    xp_drop: bool = False

    @property
    def planks(self) -> int:
        return self.inventory.count(PLANK) if self.inventory else 0

    def describe(self) -> str:
        return (
            f"client={'ok' if self.client.ok else self.client.problem} "
            f"menu={'open' if self.menu else '-'} furniture={'open' if self.furniture_open else '-'} "
            f"dialogue={self.dialogue.value} butler={'visible' if self.butler.blob else '-'} "
            f"planks={self.planks}"
        )


class Perception:
    def __init__(self, profile: Profile, settings: Settings, rules: list[Rule], order_rules: list[Rule]) -> None:
        self.profile = profile
        self.settings = settings
        self.library: TemplateLibrary = profile.templates
        self.layout = Layout(profile)
        self.client = ClientWatch.from_profile(profile, ANCHOR)
        menu_bg = profile.color("menu_background")
        self.menu = ContextMenu(MenuStyle(background=menu_bg) if menu_bg else None)
        min_px = settings.construction.hotspot_min_pixels
        self.hotspot = Highlight(_require_color(profile, "hotspot_empty"), min_pixels=min_px)
        built = profile.color("table_built")
        self.built = Highlight(built, min_pixels=min_px) if built else None
        butler = profile.color("butler")
        self.butler = Highlight(butler, min_pixels=30) if butler else None
        empty = None
        if self.library.has("inventory_empty_slot"):
            empty = EmptySlotModel.from_sample(self.library.get("inventory_empty_slot").image)
        self.inventory = Inventory({PLANK: self.library.get("item_mahogany_plank")}, empty)
        # Template sets used to classify the chatbox.
        self.safe_open = [n for n in settings.butler.safe_open_dialogues if self.library.has(n)]
        self.settle_rules = rules
        self.order_rules = order_rules
        watch = {"remove_confirm", *self.safe_open}
        for rule in rules + order_rules:
            watch |= rule.templates()
        self.dialogue_templates = sorted(n for n in watch if self.library.has(n))
        self.last_hotspot: Point | None = None

    # -- regions ---------------------------------------------------------------
    @property
    def viewport(self) -> Rect:
        return self.layout.require("viewport")

    @property
    def chatbox(self) -> Rect:
        return self.layout.require("chatbox")

    @property
    def inventory_grid(self) -> Rect:
        return self.layout.require("inventory_grid")

    # -- individual signals ----------------------------------------------------
    def find_menu(self, frame: Frame, near: Point | None = None) -> Rect | None:
        return self.menu.find(frame, near=near)

    def area(self, template: str, fallback: Rect | None = None) -> Rect | None:
        """Where to look for a fixed UI element: around its calibrated spot if known."""
        return self.layout.near(template) or fallback

    def furniture_open(self, frame: Frame) -> bool:
        name = "furniture_menu_title"
        return find_best(frame, self.library.get(name), self.area(name, self.viewport)) is not None

    def chat_idle(self, frame: Frame) -> bool:
        return find_best(frame, self.library.get("chat_idle"), self.area("chat_idle", self.chatbox)) is not None

    def visible_dialogue(self, frame: Frame) -> Visible:
        found: Visible = {}
        for name in self.dialogue_templates:
            matches = find_all(frame, self.library.get(name), self.chatbox, max_results=3)
            if matches:
                found[name] = matches
        return found

    def visible(self, frame: Frame, name: str, region: Rect | None = None) -> bool:
        """Whether a template is visible in ``region`` (default: around its calibrated spot)."""
        if not self.library.has(name):
            return False
        return find_best(frame, self.library.get(name), region or self.area(name)) is not None

    def hotspot_pick(self, frame: Frame) -> Pick:
        pick = self.hotspot.pick(frame, self.viewport, near=self.last_hotspot)
        if pick.blob is not None:
            self.last_hotspot = pick.blob.center
        return pick

    def built_pick(self, frame: Frame) -> Pick | None:
        return None if self.built is None else self.built.pick(frame, self.viewport, near=self.last_hotspot)

    def butler_pick(self, frame: Frame) -> Pick:
        return Pick(None, 0) if self.butler is None else self.butler.pick(frame, self.viewport)

    def scan_inventory(self, frame: Frame) -> InventoryScan:
        return self.inventory.scan(frame, self.inventory_grid)

    def inventory_visible(self, frame: Frame, scan: InventoryScan) -> bool:
        if self.library.has("inventory_open"):
            return self.visible(frame, "inventory_open")
        if scan.knows_empty:
            # With the background model, an inventory showing neither planks nor
            # empty slots is almost certainly another tab.
            return scan.count(PLANK) + scan.count("empty") > 0
        return True

    def table_state(self, hotspot: Pick, built: Pick | None) -> Table:
        if hotspot.ambiguous or (built is not None and built.ambiguous):
            return Table.UNKNOWN
        has_hotspot = hotspot.blob is not None
        has_built = built is not None and built.blob is not None
        if has_hotspot and not has_built:
            return Table.EMPTY
        if has_built and not has_hotspot:
            return Table.BUILT
        return Table.UNKNOWN

    def classify_dialogue(self, chat_idle: bool, visible: Visible) -> Dialogue:
        if chat_idle:
            return Dialogue.NONE
        if visible.get("remove_confirm"):
            return Dialogue.REMOVE_CONFIRM
        if any(r.applies(visible) for r in self.settle_rules):
            return Dialogue.SETTLE
        if any(visible.get(n) for n in self.safe_open):
            return Dialogue.SAFE_OPEN
        if any(r.applies(visible) for r in self.order_rules):
            return Dialogue.ORDER
        return Dialogue.UNKNOWN

    # -- full snapshot -----------------------------------------------------------
    def observe(self, frame: Frame) -> Snapshot:
        status = self.client.status(frame)
        snap = Snapshot(frame, status)
        if not status.ok:
            return snap
        self.layout.update(status)
        snap.menu = self.find_menu(frame)
        snap.furniture_open = self.furniture_open(frame)
        if snap.menu is None and not snap.furniture_open:
            snap.hotspot = self.hotspot_pick(frame)
            snap.built = self.built_pick(frame)
        snap.chat_idle = self.chat_idle(frame)
        snap.visible = {} if snap.chat_idle else self.visible_dialogue(frame)
        snap.dialogue = self.classify_dialogue(snap.chat_idle, snap.visible)
        snap.butler = self.butler_pick(frame)
        snap.inventory = self.scan_inventory(frame)
        snap.inventory_open = self.inventory_visible(frame, snap.inventory)
        snap.xp_drop = self.visible(frame, "xp_drop_construction", self.viewport)
        return snap

    # -- diagnostics -------------------------------------------------------------
    def annotate(self, snap: Snapshot) -> np.ndarray:
        image = snap.frame.image.copy()

        def box(rect: Rect | None, color: tuple[int, int, int], label: str = "") -> None:
            if rect is None:
                return
            x, y = rect.x - snap.frame.ox, rect.y - snap.frame.oy
            cv2.rectangle(image, (x, y), (x + rect.w, y + rect.h), color, 1)
            if label:
                cv2.putText(image, label, (x, max(10, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

        if snap.client.ok:
            for name in ("viewport", "chatbox", "inventory_grid", "uptext"):
                box(self.layout.region(name), (200, 200, 200), name)
            for cell in self.inventory.slot_rects(self.inventory_grid):
                box(cell, (90, 90, 90))
        box(snap.hotspot.blob.bbox if snap.hotspot.blob else None, (255, 255, 0), "table space")
        if snap.built and snap.built.blob:
            box(snap.built.blob.bbox, (255, 0, 255), "mahogany table")
        box(snap.butler.blob.bbox if snap.butler.blob else None, (0, 255, 255), "butler")
        box(snap.menu, (0, 165, 255), "menu")
        for name, matches in snap.visible.items():
            for m in matches:
                box(m.rect, (0, 255, 0), name)
        return image


def _require_color(profile: Profile, name: str) -> ColorSpec:
    color = profile.color(name)
    if color is None:
        raise ConfigError(f"colour '{name}' is not calibrated")
    return color
