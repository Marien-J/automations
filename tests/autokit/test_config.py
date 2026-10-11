from dataclasses import dataclass

import pytest

from autokit.errors import ConfigError
from autokit.runtime.registry import discover, resolve
from autokit.runtime.rules import Rule
from autokit.runtime.settings import deep_merge, parse_dataclass


@dataclass(frozen=True)
class Inner:
    a: int
    b: float


@dataclass(frozen=True)
class Outer:
    inner: Inner
    name: str


def test_deep_merge_overrides_nested_values_and_replaces_lists():
    merged = deep_merge({"a": {"x": 1, "y": [1, 2]}, "b": 2}, {"a": {"y": [3]}})
    assert merged == {"a": {"x": 1, "y": [3]}, "b": 2}


def test_parse_dataclass_rejects_unknown_and_missing_keys():
    assert parse_dataclass(Outer, {"inner": {"a": 1, "b": 2}, "name": "x"}).inner.b == 2.0
    with pytest.raises(ConfigError, match="unknown"):
        parse_dataclass(Outer, {"inner": {"a": 1, "b": 2, "c": 3}, "name": "x"})
    with pytest.raises(ConfigError, match="missing"):
        parse_dataclass(Outer, {"inner": {"a": 1}, "name": "x"})
    with pytest.raises(ConfigError, match="must be int"):
        parse_dataclass(Outer, {"inner": {"a": "1", "b": 2}, "name": "x"})


def test_rules_need_a_condition_and_exactly_one_action():
    rule = Rule.from_dict({"name": "pay", "when": ["a", "b"], "click": "b", "signal": "wage_paid"})
    assert rule.templates() == {"a", "b"} and rule.signal == "wage_paid"
    with pytest.raises(ConfigError):
        Rule.from_dict({"name": "x", "when": [], "key": "space"})
    with pytest.raises(ConfigError):
        Rule.from_dict({"name": "x", "when": ["a"], "key": "space", "click": "a"})
    with pytest.raises(ConfigError):
        Rule.from_dict({"name": "x", "when": ["a"]})
    with pytest.raises(ConfigError):
        Rule.from_dict({"name": "x", "when": ["a"], "key": "1", "typo": True})


def test_registry_discovers_automations_by_id_and_prefix():
    assert "osrs_construction_mahogany_tables" in discover()
    assert resolve("osrs_construction").id == "osrs_construction_mahogany_tables"
    with pytest.raises(ConfigError):
        resolve("does_not_exist")
