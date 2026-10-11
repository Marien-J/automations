"""End-to-end runs of the real automation against the simulated POH.

Each test checks the automation's own report against the simulator's ground
truth: what was really built, ordered, delivered, and what input was sent.
"""

from automations.osrs_construction_mahogany_tables.sim import Scenario


def assert_clean(result):
    report = result.report
    assert report["wrong_actions"] == []
    assert report["wrong_furniture_clicks"] == 0
    assert report["stray_keys"] == 0
    assert report["summons_while_away"] == 0
    assert report["orders_while_items_held"] == 0


def assert_counts_match(result):
    stats, report = result.summary.stats, result.report
    assert stats["tables_built"] == report["tables_built"]
    assert stats["tables_removed"] == report["tables_removed"]
    assert stats["butler_orders"] == report["orders"]
    assert stats["planks_delivered"] == report["planks_delivered"]


def test_single_build_and_remove_cycle(run_sim):
    result = run_sim(Scenario(max_tables=1))
    assert result.summary.completed, result.summary.stop_reason
    assert result.report["tables_built"] == 1 and result.report["tables_removed"] == 1
    assert result.report["planks_in_inventory"] == 18
    assert result.summary.stats["observed_planks_per_build"] == {6: 1}
    assert result.report["orders"] == 0  # 18 planks left is above the refill threshold
    assert_clean(result)


def test_full_replenishment_cycle(run_sim):
    result = run_sim(Scenario(planks=12, max_tables=4))
    assert result.summary.completed, result.summary.stop_reason
    assert result.report["orders"] >= 1
    assert result.events_named("order_submitted")[0]["t"] < result.events_named("table_built")[0]["t"]
    deliveries = result.events_named("planks_received")
    assert sum(e["planks"] for e in deliveries) == result.report["planks_delivered"] >= 24
    assert_counts_match(result)
    assert_clean(result)


def test_long_run_with_many_refills_and_partial_deliveries(run_sim):
    result = run_sim(Scenario(max_tables=30))
    assert result.summary.completed, result.summary.stop_reason
    report, stats = result.report, result.summary.stats
    assert report["tables_built"] == report["tables_removed"] == 30
    assert report["orders"] >= 6
    assert stats["collections_from_butler"] >= 3  # partial deliveries were collected later
    assert any(e["still_held_by_butler"] > 0 for e in result.events_named("planks_received"))
    assert stats["observed_planks_per_build"] == {6: 30}
    assert_counts_match(result)
    assert_clean(result)
    # building continued while the butler was away (except after the final order)
    for order in result.events_named("butler_departed"):
        if order["t"] > result.stopped_at() - 7.0:
            continue
        builds = [e for e in result.events_named("table_built") if order["t"] < e["t"] < order["t"] + 7.0]
        assert builds, f"no build while the butler was away after t={order['t']}"


def test_wages_are_paid_through_the_dialogue(run_sim):
    result = run_sim(Scenario(wage_every=2, max_tables=16))
    assert result.summary.completed, result.summary.stop_reason
    assert result.report["wages_paid"] >= 2
    assert result.summary.stats["wages_paid"] == result.report["wages_paid"]
    assert result.summary.stats["observed_orders_between_wages"][0] == 2
    assert_clean(result)


def test_unpaid_wages_stop_refills_without_retrying(run_sim):
    result = run_sim(Scenario(coins=0, wage_every=1, max_tables=40))
    assert not result.summary.completed
    assert "not enough coins" in result.summary.stop_reason
    assert result.world.stats["wage_attempts_without_coins"] == 1
    assert result.summary.stats["wages_paid"] == 0  # the attempt is not reported as a payment
    assert result.report["orders"] == 1
    assert result.report["tables_built"] == 24 // 6  # used up what it had, then stopped
    assert_clean(result)


def test_absent_butler_is_called_and_his_dialogue_used(run_sim):
    result = run_sim(Scenario(butler_present=False, call_opens_dialogue=True, planks=12, max_tables=6))
    assert result.summary.completed, result.summary.stop_reason
    assert result.report["summons"] >= 1
    assert result.events_named("summon_butler")
    assert_counts_match(result)
    assert_clean(result)


def test_called_butler_without_automatic_dialogue(run_sim):
    result = run_sim(Scenario(butler_present=False, call_opens_dialogue=False, planks=12, max_tables=6))
    assert result.summary.completed, result.summary.stop_reason
    assert result.report["summons"] >= 1
    assert_clean(result)


def test_bell_pull_summon(run_sim):
    result = run_sim(
        Scenario(butler_present=False, summon="bell_pull", call_opens_dialogue=False, planks=12, max_tables=6)
    )
    assert result.summary.completed, result.summary.stop_reason
    assert result.world.stats["menu:Ring Bell-pull"] >= 1
    assert_clean(result)


def test_camera_shifts_between_cycles(run_sim):
    result = run_sim(Scenario(camera_shift_px=45, max_tables=12), seed=4)
    assert result.summary.completed, result.summary.stop_reason
    assert_counts_match(result)
    assert_clean(result)


def test_variants_without_optional_calibration(run_sim):
    scenario = Scenario(with_built_marker=False, with_empty_slot_model=False, repeat_option=False, max_tables=10)
    result = run_sim(scenario)
    assert result.summary.completed, result.summary.stop_reason
    assert_counts_match(result)
    assert_clean(result)


def test_talk_via_right_click_menu(run_sim):
    result = run_sim(Scenario(planks=12, max_tables=4, settings={"butler": {"talk_mode": "menu"}}))
    assert result.summary.completed, result.summary.stop_reason
    assert result.world.stats["menu:Talk-to Demon butler"] >= 1
    assert_clean(result)


def test_unexpected_dialogue_stops_all_input(run_sim):
    result = run_sim(Scenario(unknown_dialogue_at=20.0))
    assert not result.summary.completed
    assert "unrecognised dialogue" in result.summary.stop_reason
    assert result.report["keys_into_unknown_dialogue"] == 0
    assert result.world.stats["clicks_into_unknown_dialogue"] == 0
    assert result.desktop.inputs_after(result.stopped_at()) == []


def test_disconnect_stops_after_grace_period(run_sim):
    result = run_sim(Scenario(disconnect_at=20.0))
    assert not result.summary.completed
    assert "connection lost" in result.summary.stop_reason
    # nothing new is started once the disconnect is seen; at most an in-flight click lands
    assert result.report["inputs_while_disconnected"] <= 2
    assert result.desktop.inputs_after(result.stopped_at()) == []


def test_short_disconnect_is_waited_out(run_sim):
    result = run_sim(Scenario(disconnect_at=20.0, reconnect_after=5.0, max_tables=8))
    assert result.summary.completed, result.summary.stop_reason
    assert result.events_named("client_problem")
    assert_clean(result)


def test_emergency_stop_hotkey(run_sim):
    def setup(world, stop):
        world.at(15.0, lambda: stop.trigger("hotkey <f12> pressed"))

    result = run_sim(Scenario(max_tables=30), setup=setup)
    assert not result.summary.completed
    assert "emergency stop" in result.summary.stop_reason
    assert result.desktop.inputs_after(15.0) == []


def test_user_grabbing_the_mouse_stops_the_run(run_sim):
    result = run_sim(Scenario(user_mouse_at=12.0, max_tables=30))
    assert not result.summary.completed
    assert "manual mouse movement" in result.summary.stop_reason
    assert result.desktop.inputs_after(12.05) == []


def test_missing_hotspot_stops_without_clicking(run_sim):
    result = run_sim(Scenario(build_mode=False))
    assert not result.summary.completed
    assert "cannot find the Table space" in result.summary.stop_reason
    assert not [e for e in result.desktop.events if e.kind == "button"]


def test_unavailable_mahogany_table_is_never_forced(run_sim):
    result = run_sim(Scenario(level=40))
    assert not result.summary.completed
    assert "Mahogany table" in result.summary.stop_reason
    assert result.report["tables_built"] == 0
    assert result.world.stats["unavailable_clicks"] == 0
    assert_clean(result)
