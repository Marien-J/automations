"""Typed settings. Defaults live only in ``defaults.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autokit.errors import ConfigError
from autokit.runtime.rules import Rule
from autokit.runtime.settings import parse_dataclass
from osrskit.steps import Step


@dataclass(frozen=True)
class RunSettings:
    max_tables: int
    max_minutes: float
    status_every_s: float


@dataclass(frozen=True)
class SafetySettings:
    emergency_hotkey: str
    stop_file: str
    manual_mouse_tolerance_px: float
    max_consecutive_failures: int
    disconnect_grace_s: float
    unrecognised_grace_s: float


@dataclass(frozen=True)
class TimingSettings:
    poll_interval_s: float
    menu_timeout_s: float
    furniture_menu_timeout_s: float
    build_timeout_s: float
    remove_timeout_s: float
    dialogue_open_timeout_s: float
    dialogue_step_timeout_s: float
    dialogue_timeout_s: float
    dialogue_settle_s: float
    departure_timeout_s: float
    wait_poll_s: float


@dataclass(frozen=True)
class ConstructionSettings:
    planks_per_table: int
    remove_confirm_key: str
    close_interface_key: str
    hotspot_min_pixels: int
    require_xp_drop: bool


@dataclass(frozen=True)
class InventorySettings:
    other_items: int


@dataclass(frozen=True)
class ButlerSettings:
    enabled: bool
    refill_threshold: int
    request_quantity: int
    max_carry: int
    expected_trip_s: float
    trip_timeout_s: float
    arrival_timeout_s: float
    max_order_attempts: int
    max_summons_per_trip: int
    pay_wages: bool
    talk_mode: str
    use_repeat_option: bool
    repeat_option_quantity: int
    summon_method: str
    summon_steps: dict
    safe_open_dialogues: list
    rules: list
    order_rules: list


@dataclass(frozen=True)
class Settings:
    run: RunSettings
    safety: SafetySettings
    timing: TimingSettings
    mouse: dict
    construction: ConstructionSettings
    inventory: InventorySettings
    butler: ButlerSettings

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        settings = parse_dataclass(cls, data)
        settings.check()
        return settings

    def check(self) -> None:
        b = self.butler
        ppt = self.construction.planks_per_table
        if not 0 < b.request_quantity <= b.max_carry:
            raise ConfigError(f"butler.request_quantity must be 1..{b.max_carry} (max_carry)")
        if b.request_quantity < ppt:
            raise ConfigError("butler.request_quantity must be at least construction.planks_per_table")
        if b.refill_threshold < 0:
            raise ConfigError("butler.refill_threshold must be >= 0")
        if b.talk_mode not in ("left_click", "menu"):
            raise ConfigError("butler.talk_mode must be 'left_click' or 'menu'")
        if b.summon_method != "none" and b.summon_method not in b.summon_steps:
            raise ConfigError(f"butler.summon_method '{b.summon_method}' has no entry in butler.summon_steps")
        self.rules()
        self.order_rules()
        self.summon_steps()

    # -- parsed views ----------------------------------------------------------
    def rules(self) -> list[Rule]:
        rules = [Rule.from_dict(r) for r in self.butler.rules]
        if not self.butler.pay_wages:
            # Never click "pay"; recognising a wage request then ends refills cleanly.
            rules = [r for r in rules if r.signal != "wage_paid"]
            rules.insert(
                0, Rule("wages due", ("butler_wage_request",), fail="butler wages are due and pay_wages is off")
            )
        return rules

    def order_rules(self) -> list[Rule]:
        rules = [Rule.from_dict(r) for r in self.butler.order_rules]
        repeat_ok = self.butler.use_repeat_option and self.butler.repeat_option_quantity == self.butler.request_quantity
        if not repeat_ok:
            rules = [r for r in rules if "butler_opt_repeat_order" not in r.templates()]
        return rules

    def summon_steps(self) -> list[Step]:
        if self.butler.summon_method == "none":
            return []
        return [Step.from_dict(s) for s in self.butler.summon_steps[self.butler.summon_method]]
