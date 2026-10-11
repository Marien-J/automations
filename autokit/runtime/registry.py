"""Discovers automations: every sub-package of ``automations`` that exports
``AUTOMATION`` (an :class:`~autokit.runtime.automation.Automation` subclass)."""

from __future__ import annotations

import importlib
import pkgutil

from autokit.errors import ConfigError
from autokit.runtime.automation import Automation


def discover() -> dict[str, type[Automation]]:
    import automations

    found: dict[str, type[Automation]] = {}
    for info in pkgutil.iter_modules(automations.__path__):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"automations.{info.name}")
        cls = getattr(module, "AUTOMATION", None)
        if cls is None:
            continue
        if not (isinstance(cls, type) and issubclass(cls, Automation)):
            raise ConfigError(f"automations.{info.name}.AUTOMATION is not an Automation subclass")
        if cls.id in found:
            raise ConfigError(f"duplicate automation id '{cls.id}'")
        found[cls.id] = cls
    return found


def resolve(name: str) -> type[Automation]:
    """Look up an automation by id or unique id prefix."""
    available = discover()
    if name in available:
        return available[name]
    candidates = [key for key in available if key.startswith(name)]
    if len(candidates) == 1:
        return available[candidates[0]]
    if not candidates:
        raise ConfigError(f"no automation named '{name}'; available: {', '.join(sorted(available)) or 'none'}")
    raise ConfigError(f"'{name}' is ambiguous: {', '.join(sorted(candidates))}")
