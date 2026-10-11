"""Low-level OS input. Automations never use a backend directly; they go
through :class:`autokit.input.controller.InputController`, which enforces the
emergency stop and human-paced movement."""

from __future__ import annotations

from typing import Protocol

# Friendly names accepted by every backend, mapped to pynput's names.
_KEY_ALIASES = {"escape": "esc", "return": "enter", "del": "delete"}


class InputBackend(Protocol):
    def position(self) -> tuple[int, int]: ...

    def move(self, x: int, y: int) -> None: ...

    def button(self, button: str, down: bool) -> None:
        """``button`` is ``"left"``, ``"right"`` or ``"middle"``."""

    def key(self, key: str, down: bool) -> None:
        """``key`` is a single character or a name such as ``"space"``, ``"enter"``, ``"escape"``, ``"f5"``."""


class PynputBackend:
    def __init__(self) -> None:
        from pynput import keyboard, mouse  # lazy: needs a desktop session

        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()
        self._buttons = {"left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle}
        self._keys = keyboard.Key

    def position(self) -> tuple[int, int]:
        x, y = self._mouse.position
        return int(x), int(y)

    def move(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)

    def button(self, button: str, down: bool) -> None:
        target = self._buttons[button]
        if down:
            self._mouse.press(target)
        else:
            self._mouse.release(target)

    def key(self, key: str, down: bool) -> None:
        target = key if len(key) == 1 else getattr(self._keys, _KEY_ALIASES.get(key, key))
        if down:
            self._keyboard.press(target)
        else:
            self._keyboard.release(target)
