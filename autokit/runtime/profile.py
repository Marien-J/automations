"""A profile is one user's calibrated setup for one automation.

Layout (``profiles/<automation-id>/<profile-name>/``)::

    settings.yaml       user overrides of the automation's defaults.yaml
    calibration.json    regions, colours, template options, anchor position
    templates/*.png     reference images captured from the user's client
    runs/<timestamp>/   logs and diagnostic screenshots of each run
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autokit.errors import ConfigError
from autokit.geometry import Rect
from autokit.runtime.settings import deep_merge, load_yaml
from autokit.vision.color import ColorSpec
from autokit.vision.templates import TemplateLibrary


class Profile:
    def __init__(
        self, root: Path, settings: dict[str, Any], calibration: dict[str, Any], templates: TemplateLibrary
    ) -> None:
        self.root = root
        self.settings = settings
        self.calibration = calibration
        self.templates = templates

    @classmethod
    def load(cls, root: Path, defaults_file: Path) -> Profile:
        settings = deep_merge(load_yaml(defaults_file), load_yaml(root / "settings.yaml"))
        calibration: dict[str, Any] = {}
        cal_path = root / "calibration.json"
        if cal_path.exists():
            try:
                calibration = json.loads(cal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{cal_path} is not valid JSON: {exc}") from exc
        templates = TemplateLibrary.load(root / "templates", calibration.get("templates", {}))
        return cls(root, settings, calibration, templates)

    # -- paths ---------------------------------------------------------------
    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    # -- calibrated values ---------------------------------------------------
    def region(self, name: str) -> Rect | None:
        value = self.calibration.get("regions", {}).get(name)
        return Rect.from_list(value) if value else None

    def color(self, name: str) -> ColorSpec | None:
        value = self.calibration.get("colors", {}).get(name)
        return ColorSpec.from_value(value) if value else None

    def template_home(self, name: str) -> Rect | None:
        """Where a template was captured (calibration coordinates), if recorded."""
        value = self.calibration.get("templates", {}).get(name, {}).get("rect")
        return Rect.from_list(value) if value else None

    def set_template_home(self, name: str, rect: Rect) -> None:
        self.calibration.setdefault("templates", {}).setdefault(name, {})["rect"] = rect.to_list()

    @property
    def anchor_rect(self) -> Rect | None:
        value = self.calibration.get("anchor", {}).get("rect")
        return Rect.from_list(value) if value else None

    def set_region(self, name: str, rect: Rect) -> None:
        self.calibration.setdefault("regions", {})[name] = rect.to_list()

    def set_color(self, name: str, color: ColorSpec) -> None:
        self.calibration.setdefault("colors", {})[name] = color.to_dict()

    def set_anchor(self, template: str, rect: Rect) -> None:
        self.calibration["anchor"] = {"template": template, "rect": rect.to_list()}

    def save_calibration(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "calibration.json").write_text(json.dumps(self.calibration, indent=2) + "\n", encoding="utf-8")
