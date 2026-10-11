"""Screen sources: the real screen (via ``mss``) or a static image."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from autokit.geometry import Rect
from autokit.vision.frame import Frame


class ScreenSource(Protocol):
    @property
    def bounds(self) -> Rect:
        """The capturable area in screen coordinates."""

    def grab(self, region: Rect | None = None) -> Frame:
        """Capture ``region`` (or everything) as a :class:`Frame`."""


class MssScreen:
    """Captures a monitor with ``mss``. ``monitor=0`` is the whole virtual desktop."""

    def __init__(self, monitor: int = 1, region: Rect | None = None) -> None:
        import mss  # imported lazily so tests/simulations don't need a display

        self._sct = mss.mss()
        mon = self._sct.monitors[monitor]
        full = Rect(mon["left"], mon["top"], mon["width"], mon["height"])
        self._bounds = full.intersection(region) if region else full
        if self._bounds is None:
            raise ValueError(f"capture region {region} is outside monitor {monitor} {full}")

    @property
    def bounds(self) -> Rect:
        return self._bounds

    def grab(self, region: Rect | None = None) -> Frame:
        target = self._bounds if region is None else self._bounds.intersection(region)
        if target is None:
            raise ValueError(f"region {region} is outside the capture bounds {self._bounds}")
        shot = self._sct.grab({"left": target.x, "top": target.y, "width": target.w, "height": target.h})
        image = np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR
        return Frame(image, target.x, target.y)


class ImageScreen:
    """A fixed screenshot, e.g. for ``autokit check --image``."""

    def __init__(self, image: np.ndarray, ox: int = 0, oy: int = 0) -> None:
        self._frame = Frame(image, ox, oy)

    @classmethod
    def from_file(cls, path: Path) -> ImageScreen:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(path)
        return cls(image)

    @property
    def bounds(self) -> Rect:
        return self._frame.rect

    def grab(self, region: Rect | None = None) -> Frame:
        frame = self._frame.crop(region)
        if frame is None:
            raise ValueError(f"region {region} is outside the image")
        return frame
