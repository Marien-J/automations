"""Emergency stop.

One latching :class:`EmergencyStop` is shared by everything that can send
input. Once triggered it never resets for the lifetime of a run; the input
controller checks it before every mouse step, button and key event, so no game
input is sent after the trigger.

Triggers:

* a global hotkey (:class:`HotkeyWatcher`, via ``pynput``; best effort),
* a stop file appearing on disk (:class:`StopFileWatcher`; always works),
* the user moving the mouse away from where the automation put it
  (detected by :class:`autokit.input.controller.InputController`),
* Ctrl+C in the terminal, unsafe states and fatal errors (via the runner).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from autokit.clock import Clock
from autokit.errors import EmergencyStopped

log = logging.getLogger(__name__)

SLEEP_CHUNK_S = 0.05


class EmergencyStop:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    def trigger(self, reason: str, *, announce: bool = True) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()
        if announce:
            log.warning("EMERGENCY STOP: %s", reason)

    @property
    def triggered(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def check(self) -> None:
        """Raise :class:`EmergencyStopped` if the stop has been triggered."""
        if self._event.is_set():
            raise EmergencyStopped(self._reason or "emergency stop")

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


def interruptible_sleep(clock: Clock, stop: EmergencyStop, seconds: float) -> None:
    """Sleep in small chunks, raising :class:`EmergencyStopped` as soon as stopped."""
    end = clock.now() + seconds
    while True:
        stop.check()
        remaining = end - clock.now()
        if remaining <= 0:
            return
        clock.sleep(min(remaining, SLEEP_CHUNK_S))


class StopFileWatcher:
    """Triggers the stop when ``path`` exists. Polls from a daemon thread."""

    def __init__(self, stop: EmergencyStop, path: Path, interval: float = 0.2) -> None:
        self._stop = stop
        self.path = path
        self._interval = interval
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stop-file", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._closed.wait(self._interval):
            if self.path.exists():
                self._stop.trigger(f"stop file {self.path} present")
                return

    def close(self) -> None:
        self._closed.set()


class HotkeyWatcher:
    """Global hotkey (pynput syntax, e.g. ``"<f12>"`` or ``"<ctrl>+<shift>+x"``)."""

    def __init__(self, stop: EmergencyStop, hotkey: str) -> None:
        self._stop = stop
        self.hotkey = hotkey
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:  # pragma: no cover - depends on the desktop session
            log.warning("emergency hotkey unavailable (%s); use the stop file or move the mouse", exc)
            return False
        try:
            self._listener = keyboard.GlobalHotKeys(
                {self.hotkey: lambda: self._stop.trigger(f"hotkey {self.hotkey} pressed")}
            )
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:  # pragma: no cover - depends on the desktop session
            log.warning("could not register emergency hotkey %s (%s)", self.hotkey, exc)
            return False
        return True

    def close(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
