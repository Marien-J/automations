"""Draw OSRS-like widgets onto BGR numpy images.

These are deliberately simple approximations (bitmap font, flat colours) of
the real client's menus, chatbox dialogues and inventory. They exist so the
full automation pipeline — template matching, colour detection, verified
clicking — can be exercised end-to-end without a game client.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from autokit.geometry import Rect

RGB = tuple[int, int, int]

MENU_BG: RGB = (93, 84, 71)
WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)
OBJECT_CYAN: RGB = (0, 255, 255)
NPC_YELLOW: RGB = (255, 255, 0)
CHAT_BG: RGB = (190, 175, 135)
CHAT_TITLE: RGB = (128, 0, 0)
CHAT_BLUE: RGB = (0, 0, 255)
PANEL_BG: RGB = (62, 53, 41)
MENU_ROW_H = 15
MENU_HEADER_H = 19


def _font() -> ImageFont.ImageFont:
    loader = getattr(ImageFont, "load_default_imagefont", None)
    return loader() if loader else ImageFont.load_default()


_FONT = _font()


def bgr(color: RGB) -> tuple[int, int, int]:
    r, g, b = color
    return b, g, r


@lru_cache(maxsize=1024)
def text_mask(text: str) -> np.ndarray:
    _, _, right, bottom = _FONT.getbbox(text)
    image = Image.new("L", (max(1, right), max(1, bottom)))
    ImageDraw.Draw(image).text((0, 0), text, font=_FONT, fill=255)
    return np.array(image) > 127


def text_size(text: str) -> tuple[int, int]:
    h, w = text_mask(text).shape
    return w, h


def draw_text(img: np.ndarray, x: int, y: int, text: str, color: RGB) -> Rect:
    mask = text_mask(text)
    h, w = mask.shape
    region = img[y : y + h, x : x + w]
    region[mask[: region.shape[0], : region.shape[1]]] = bgr(color)
    return Rect(x, y, w, h)


def fill(img: np.ndarray, rect: Rect, color: RGB) -> None:
    img[rect.y : rect.bottom, rect.x : rect.right] = bgr(color)


def outline(img: np.ndarray, rect: Rect, color: RGB, thickness: int = 1) -> None:
    cv2.rectangle(img, (rect.x, rect.y), (rect.right - 1, rect.bottom - 1), bgr(color), thickness)


def draw_label(img: np.ndarray, x: int, y: int, parts: list[tuple[str, RGB]]) -> Rect:
    """Draw ``[(text, colour), ...]`` side by side separated by spaces."""
    cursor = x
    height = 0
    for i, (text, color) in enumerate(parts):
        piece = text if i == len(parts) - 1 else text + " "
        rect = draw_text(img, cursor, y, piece, color)
        cursor = rect.right
        height = max(height, rect.h)
    return Rect(x, y, cursor - x, height)


def menu_size(entries: list[tuple[str, str, RGB]]) -> tuple[int, int]:
    widths = [text_size("Choose Option")[0]]
    widths += [text_size(f"{action} {target}".strip())[0] for action, target, _ in entries]
    return max(widths) + 8, MENU_HEADER_H + MENU_ROW_H * len(entries) + 2


def draw_context_menu(img: np.ndarray, x: int, y: int, entries: list[tuple[str, str, RGB]]) -> list[tuple[Rect, Rect]]:
    """Classic right-click menu at (x, y); returns ``(row, label)`` rectangles per entry."""
    w, h = menu_size(entries)
    fill(img, Rect(x, y, w, h), MENU_BG)
    fill(img, Rect(x + 1, y + 1, w - 2, 16), BLACK)
    draw_text(img, x + 3, y + 3, "Choose Option", MENU_BG)
    outline(img, Rect(x + 1, y + 18, w - 2, h - 19), BLACK)
    rows = []
    for i, (action, target, color) in enumerate(entries):
        row = menu_row(x, y, w, i)
        parts = [(action, WHITE)] + ([(target, color)] if target else [])
        rows.append((row, draw_label(img, row.x + 2, row.y + 2, parts)))
    return rows


def menu_row(x: int, y: int, w: int, index: int) -> Rect:
    return Rect(x + 2, y + MENU_HEADER_H + index * MENU_ROW_H, w - 4, MENU_ROW_H)


def chat_option_rects(area: Rect, options: list[str]) -> list[Rect]:
    spacing = min(20, (area.h - 30) // max(1, len(options)))
    rects = []
    for i, option in enumerate(options):
        ow, oh = text_size(option)
        rects.append(Rect(area.x + (area.w - ow) // 2, area.y + 30 + i * spacing, ow, oh))
    return rects


def _centered(img: np.ndarray, area: Rect, y: int, text: str, color: RGB) -> Rect:
    tw, _ = text_size(text)
    return draw_text(img, area.x + (area.w - tw) // 2, y, text, color)


def draw_chat_options(
    img: np.ndarray, area: Rect, title: str, options: list[str], hover: int | None = None
) -> dict[str, Rect]:
    """An options dialogue (hovered option drawn white); returns label rectangles by text."""
    fill(img, area, CHAT_BG)
    drawn = {title: _centered(img, area, area.y + 8, title, CHAT_TITLE)}
    for i, (option, rect) in enumerate(zip(options, chat_option_rects(area, options), strict=True)):
        drawn[option] = draw_text(img, rect.x, rect.y, option, WHITE if hover == i else BLACK)
    return drawn


def draw_chat_message(img: np.ndarray, area: Rect, speaker: str, lines: list[str]) -> dict[str, Rect]:
    fill(img, area, CHAT_BG)
    drawn = {speaker: _centered(img, area, area.y + 8, speaker, CHAT_TITLE)}
    for i, line in enumerate(lines):
        drawn[line] = _centered(img, area, area.y + 28 + i * 16, line, BLACK)
    drawn["Click here to continue"] = _centered(img, area, area.bottom - 22, "Click here to continue", CHAT_BLUE)
    return drawn


def draw_chat_prompt(img: np.ndarray, area: Rect, prompt: str, typed: str) -> dict[str, Rect]:
    fill(img, area, CHAT_BG)
    drawn = {prompt: _centered(img, area, area.y + 40, prompt, BLACK)}
    drawn["typed"] = _centered(img, area, area.y + 60, f"{typed}*", CHAT_BLUE)
    return drawn


def draw_chat_idle(img: np.ndarray, area: Rect, player: str, history: list[str]) -> Rect:
    """Chat history plus the input line; returns the input line's rectangle."""
    fill(img, area, CHAT_BG)
    for i, line in enumerate(history[-5:]):
        draw_text(img, area.x + 6, area.y + 8 + i * 16, line, BLACK)
    name = draw_text(img, area.x + 6, area.bottom - 18, f"{player}:", BLACK)
    star = draw_text(img, name.right + 4, area.bottom - 18, "*", CHAT_BLUE)
    return Rect(name.x, name.y, star.right - name.x, max(name.h, star.h))


# -- item icons ---------------------------------------------------------------


def draw_item(img: np.ndarray, slot: Rect, item: str) -> None:
    """Simple, distinct 32x28 icons centred in ``slot``."""
    cx, cy = slot.x + slot.w // 2, slot.y + slot.h // 2
    if item == "mahogany_plank":
        pts = np.array([[cx - 14, cy + 4], [cx + 8, cy - 11], [cx + 14, cy - 5], [cx - 8, cy + 10]], np.int32)
        cv2.fillPoly(img, [pts], bgr((120, 52, 38)))
        cv2.polylines(img, [pts], True, bgr((40, 16, 10)), 1)
        cv2.line(img, (cx - 9, cy + 3), (cx + 9, cy - 8), bgr((150, 72, 52)), 1)
    elif item == "hammer":
        cv2.line(img, (cx - 10, cy + 10), (cx + 6, cy - 6), bgr((110, 80, 40)), 3)
        cv2.rectangle(img, (cx + 1, cy - 12), (cx + 12, cy - 4), bgr((150, 150, 150)), -1)
        cv2.rectangle(img, (cx + 1, cy - 12), (cx + 12, cy - 4), bgr((30, 30, 30)), 1)
    elif item == "saw":
        pts = np.array([[cx - 13, cy + 6], [cx + 8, cy - 8], [cx + 12, cy - 2], [cx - 9, cy + 10]], np.int32)
        cv2.fillPoly(img, [pts], bgr((200, 200, 205)))
        cv2.polylines(img, [pts], True, bgr((30, 30, 30)), 1)
        cv2.rectangle(img, (cx + 6, cy - 12), (cx + 14, cy - 6), bgr((130, 60, 30)), -1)
    elif item == "coins":
        for dx, dy in ((-5, 4), (3, 2), (-1, -4)):
            cv2.circle(img, (cx + dx, cy + dy), 6, bgr((230, 190, 30)), -1)
            cv2.circle(img, (cx + dx, cy + dy), 6, bgr((90, 70, 0)), 1)
    else:
        cv2.circle(img, (cx, cy), 9, bgr((200, 40, 200)), -1)
