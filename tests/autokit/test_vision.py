import random

import cv2
import numpy as np

from autokit.geometry import Rect
from autokit.vision.color import ColorSpec, find_blobs
from autokit.vision.frame import Frame
from autokit.vision.templates import Template, find_all, still_at
from osrskit.testing import widgets as w


def _canvas(h: int = 200, wd: int = 300) -> np.ndarray:
    rng = np.random.default_rng(0)
    return np.clip(rng.integers(60, 110, (h, wd, 3)), 0, 255).astype(np.uint8)


def test_outline_blob_is_filled_and_click_points_stay_inside():
    img = _canvas()
    poly = np.array([(60, 60), (200, 50), (230, 140), (80, 150)], np.int32)
    cv2.polylines(img, [poly], True, (255, 255, 0), 2)  # cyan in BGR
    frame = Frame(img, 100, 50)
    blobs = find_blobs(frame, ColorSpec((0, 255, 255), 10))
    assert len(blobs) == 1
    blob = blobs[0]
    rng = random.Random(1)
    for _ in range(50):
        p = blob.pick_point(rng)
        local = (float(p.x - 100), float(p.y - 50))
        assert cv2.pointPolygonTest(poly.reshape(-1, 1, 2), local, True) > 8  # well inside, never on the edge


def test_find_all_returns_each_occurrence_once():
    img = np.full((120, 300, 3), 40, np.uint8)
    for x in (10, 120, 220):
        w.draw_text(img, x, 50, "Mahogany", (255, 255, 255))
    tpl_img = img[48:63, 8:62].copy()
    matches = find_all(Frame(img), Template("m", tpl_img))
    assert sorted(m.rect.x for m in matches) == [8, 118, 218]


def test_recoloured_label_does_not_match():
    img = np.full((40, 200, 3), 60, np.uint8)
    ok = w.draw_text(img, 10, 10, "Mahogany table", (255, 152, 31))
    template = Template("entry", img[ok.y : ok.bottom, ok.x : ok.right].copy())
    red = np.full((40, 200, 3), 60, np.uint8)
    w.draw_text(red, 10, 10, "Mahogany table", (200, 40, 40))
    assert find_all(Frame(img), template)
    assert not find_all(Frame(red), template)


def test_isolated_label_does_not_match_inside_longer_text():
    img = np.full((60, 400, 3), (135, 175, 190), np.uint8)
    label = w.draw_text(img, 150, 5, "Mahogany planks", (0, 0, 0))
    template = Template("opt", img[label.y : label.bottom, label.x : label.right].copy(), isolated=True)
    longer = np.full((60, 400, 3), (135, 175, 190), np.uint8)
    w.draw_text(longer, 40, 5, "Fetch from bank: 24 x Mahogany planks", (0, 0, 0))
    assert find_all(Frame(img), template)
    assert not find_all(Frame(longer), template)
    # without isolation the substring would match
    assert find_all(Frame(longer), Template("opt", template.image))


def test_still_at_tolerates_hover_recolouring_but_not_other_text():
    area = Rect(0, 0, 300, 100)
    img = np.zeros((100, 300, 3), np.uint8)
    rects = w.draw_chat_options(img, area, "Title", ["Fetch from bank", "Nevermind"])
    r = rects["Fetch from bank"]
    template = Template("fetch", img[r.y : r.bottom, r.x : r.right].copy())
    hovered = np.zeros_like(img)
    w.draw_chat_options(hovered, area, "Title", ["Fetch from bank", "Nevermind"], hover=0)
    other = np.zeros_like(img)
    w.draw_chat_options(other, area, "Title", ["Something else!", "Nevermind"])
    assert still_at(Frame(img), template, r)
    assert still_at(Frame(hovered), template, r)
    assert not still_at(Frame(other), template, r)


def test_label_differing_by_one_word_does_not_match():
    img = np.full((40, 300, 3), (71, 84, 93), np.uint8)
    label = w.draw_label(img, 10, 10, [("Build", w.WHITE), ("Table space", w.OBJECT_CYAN)])
    template = Template("build", img[label.y : label.bottom, label.x : label.right].copy())
    other = np.full((40, 300, 3), (71, 84, 93), np.uint8)
    w.draw_label(other, 10, 10, [("Build", w.WHITE), ("Chair space", w.OBJECT_CYAN)])
    assert find_all(Frame(img), template)
    assert not find_all(Frame(other), template)
    assert not still_at(Frame(other), template, label)
