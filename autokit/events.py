"""Run log: human-readable console lines plus a structured ``events.jsonl``."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import cv2

from autokit.clock import Clock
from autokit.vision.frame import Frame


class RunLog:
    def __init__(self, clock: Clock, directory: Path | None = None, name: str = "autokit.run") -> None:
        self.clock = clock
        self.directory = directory
        self._logger = logging.getLogger(name)
        self._events = None
        self._frames = 0
        self.history: list[dict[str, Any]] = []
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            self._events = (directory / "events.jsonl").open("a", encoding="utf-8")

    def event(self, kind: str, **fields: Any) -> None:
        record = {"t": round(self.clock.now(), 3), "wall": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **fields}
        self.history.append(record)
        if self._events is not None:
            self._events.write(json.dumps(record, default=str) + "\n")
            self._events.flush()
        details = " ".join(f"{k}={v}" for k, v in fields.items())
        self._logger.info("[%8.2fs] %s %s", self.clock.now(), kind, details)

    def info(self, message: str, *args: Any) -> None:
        self._logger.info("[%8.2fs] " + message, self.clock.now(), *args)

    def warning(self, message: str, *args: Any) -> None:
        self._logger.warning("[%8.2fs] " + message, self.clock.now(), *args)

    def save_frame(self, frame: Frame, label: str) -> Path | None:
        """Keep a screenshot for diagnosis (e.g. of an unrecognised state)."""
        if self.directory is None:
            return None
        self._frames += 1
        path = self.directory / f"{self._frames:03d}_{label}.png"
        cv2.imwrite(str(path), frame.image)
        return path

    def close(self) -> None:
        if self._events is not None:
            self._events.close()
            self._events = None
