import numpy as np

from autokit.geometry import Point, Rect
from autokit.vision.frame import Frame
from autokit.vision.templates import Template
from osrskit.context_menu import ContextMenu
from osrskit.inventory import EmptySlotModel, Inventory
from osrskit.testing import widgets as w

ENTRIES = [
    ("Walk here", "", w.WHITE),
    ("Examine", "Table space", w.OBJECT_CYAN),
    ("Build", "Table space", w.OBJECT_CYAN),
    ("Cancel", "", w.WHITE),
]


def _scene() -> np.ndarray:
    rng = np.random.default_rng(1)
    base = np.empty((400, 600, 3), np.int16)
    base[:] = w.bgr((100, 72, 48))
    return np.clip(base + rng.integers(-6, 7, base.shape), 0, 255).astype(np.uint8)


def test_menu_is_found_where_it_was_opened_and_only_there():
    img = _scene()
    rows = w.draw_context_menu(img, 200, 120, ENTRIES)
    mw, mh = w.menu_size(ENTRIES)
    menu = ContextMenu()
    assert menu.find(Frame(img), near=Point(250, 128)) == Rect(200, 120, mw, mh)
    assert menu.find(Frame(img), near=Point(20, 380)) is None
    assert menu.find(Frame(_scene())) is None
    label = rows[2][1]
    build = Template("build", img[label.y : label.bottom, label.x : label.right].copy(), isolated=True)
    found = menu.option(Frame(img), Rect(200, 120, mw, mh), build)
    assert len(found) == 1 and found[0].rect == label


def test_menu_option_absent_when_menu_lists_other_object():
    img = _scene()
    rows = w.draw_context_menu(img, 200, 120, ENTRIES)
    label = rows[2][1]
    build = Template("build", img[label.y : label.bottom, label.x : label.right].copy(), isolated=True)
    other = _scene()
    entries = [("Walk here", "", w.WHITE), ("Build", "Chair space", w.OBJECT_CYAN), ("Cancel", "", w.WHITE)]
    w.draw_context_menu(other, 200, 120, entries)
    rect = ContextMenu().find(Frame(other))
    assert rect is not None
    assert ContextMenu().option(Frame(other), rect, build) == []


def test_inventory_scan_counts_items_and_free_slots():
    img = np.zeros((300, 220, 3), np.uint8)
    w.fill(img, Rect(0, 0, 220, 300), w.PANEL_BG)
    grid = Rect(20, 20, 162, 248)

    def slot(i: int) -> Rect:
        return Rect(grid.x + (i % 4) * 42, grid.y + (i // 4) * 36, 36, 32)

    contents = ["hammer", "saw", "coins"] + ["mahogany_plank"] * 11
    for i, item in enumerate(contents):
        w.draw_item(img, slot(i), item)
    plank = slot(3).expand(-2)
    empty = slot(27).expand(-3)
    inv = Inventory(
        {"plank": Template("p", img[plank.y : plank.bottom, plank.x : plank.right].copy())},
        EmptySlotModel.from_sample(img[empty.y : empty.bottom, empty.x : empty.right]),
    )
    scan = inv.scan(Frame(img), grid)
    assert scan.count("plank") == 11
    assert scan.free == 28 - 14
    assert scan.count("other") == 3
