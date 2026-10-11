"""Strict settings loading: YAML defaults merged with a user's overrides, then
parsed into dataclasses. Unknown keys are errors so typos never go unnoticed."""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

from autokit.errors import ConfigError

T = TypeVar("T")


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings; lists and scalars in ``override`` replace."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def parse_dataclass(cls: type[T], data: Mapping[str, Any], path: str = "") -> T:
    """Build dataclass ``cls`` from ``data``; nested dataclass fields recurse."""
    if not isinstance(data, Mapping):
        raise ConfigError(f"setting '{path or cls.__name__}' must be a mapping")
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = set(data) - set(fields)
    if unknown:
        raise ConfigError(f"unknown setting(s) under '{path or '<root>'}': {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, field in fields.items():
        key = f"{path}.{name}" if path else name
        if name not in data:
            if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                raise ConfigError(f"missing setting '{key}'")
            continue
        hint = hints[name]
        value = data[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = parse_dataclass(hint, value, key)  # type: ignore[arg-type]
        elif hint is float and isinstance(value, int) and not isinstance(value, bool):
            kwargs[name] = float(value)
        elif hint in (int, float, str, bool) and not isinstance(value, hint):
            raise ConfigError(f"setting '{key}' must be {hint.__name__}, got {value!r}")
        else:
            kwargs[name] = value
    return cls(**kwargs)
