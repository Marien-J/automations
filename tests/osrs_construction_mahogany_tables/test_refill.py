import pytest
import yaml

from autokit.errors import ConfigError
from autokit.runtime.settings import deep_merge
from automations.osrs_construction_mahogany_tables.automation import MahoganyTables
from automations.osrs_construction_mahogany_tables.refill import PlankLedger, Refill
from automations.osrs_construction_mahogany_tables.settings import Settings


def _settings(**butler) -> Settings:
    defaults = yaml.safe_load(MahoganyTables.defaults_file().read_text())
    return Settings.from_dict(deep_merge(defaults, {"butler": butler}))


def test_ledger_separates_builds_from_deliveries():
    ledger = PlankLedger()
    assert ledger.observe(24) == 0
    ledger.consume(6)
    assert ledger.observe(18) == 0  # a build, fully explained
    ledger.consume(6)
    assert ledger.observe(31) == 19  # build + delivery of 19 in the same interval
    assert ledger.observe(25) == -6  # unexplained drop is reported, not hidden


def test_order_only_when_no_trip_and_at_or_below_threshold():
    refill = Refill(_settings().butler)
    assert not refill.wants_order(13)
    assert refill.wants_order(12)
    refill.order_submitted(24, now=0.0)
    assert not refill.wants_order(0)  # never duplicate while a trip is pending
    with pytest.raises(RuntimeError):
        refill.order_submitted(24, now=1.0)


def test_trip_lifecycle_with_partial_delivery():
    refill = Refill(_settings().butler)
    refill.order_submitted(24, now=0.0)
    assert refill.update(0.5, butler_visible=True, chat_idle=True, departure_timeout=4) is None
    assert refill.update(1.0, butler_visible=False, chat_idle=True, departure_timeout=4) == "departed"
    assert not refill.overdue(20.0)
    assert refill.delivered(19, now=9.0) is False
    assert refill.trip.outstanding == 5
    assert not refill.wants_collect(free_slots=0)  # nowhere to put them yet
    assert refill.wants_collect(free_slots=6)
    assert refill.delivered(5, now=15.0) is True
    assert refill.trip is None and refill.history[-1].received == 24


def test_order_not_taken_and_overdue_detection():
    s = _settings(max_order_attempts=2, trip_timeout_s=30)
    refill = Refill(s.butler)
    refill.order_submitted(24, now=0.0)
    assert refill.update(5.0, butler_visible=True, chat_idle=True, departure_timeout=4) == "order_not_taken"
    refill.order_failed()
    assert refill.enabled
    refill.order_submitted(24, now=10.0)
    refill.update(10.5, butler_visible=False, chat_idle=True, departure_timeout=4)
    assert not refill.overdue(39.0) and refill.overdue(41.0)
    refill.order_failed()
    refill.order_failed()
    assert not refill.enabled and "failed" in refill.disabled_reason


def test_settings_validation():
    with pytest.raises(ConfigError, match="request_quantity"):
        _settings(request_quantity=30)
    with pytest.raises(ConfigError, match="talk_mode"):
        _settings(talk_mode="shout")


def test_repeat_option_is_only_used_for_the_requested_quantity():
    names = [r.name for r in _settings().order_rules()]
    assert "repeat previous order" in names
    names = [r.name for r in _settings(request_quantity=18).order_rules()]
    assert "repeat previous order" not in names


def test_wages_are_never_paid_when_disabled():
    rules = _settings(pay_wages=False).rules()
    assert all(r.signal != "wage_paid" for r in rules)
    assert rules[0].fail and rules[0].when == ("butler_wage_request",)
