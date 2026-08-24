from scripts import urgent_strategy_factory_review as urgent


def _base_evidence():
    return {
        "operator_urgent_no_trade_report": False,
        "missing_target_strategies": [],
        "strategies_not_in_real_money_validation": [],
    }


def test_operator_urgent_always_escalates():
    evidence = _base_evidence()
    evidence["operator_urgent_no_trade_report"] = True
    assert urgent.needs_urgent_review(evidence) is True


def test_missing_target_strategy_escalates():
    evidence = _base_evidence()
    evidence["missing_target_strategies"] = ["Flow Acceleration"]
    assert urgent.needs_urgent_review(evidence) is True


def test_unpromoted_target_strategy_escalates():
    evidence = _base_evidence()
    evidence["strategies_not_in_real_money_validation"] = ["Cross Venue Net Arbitrage"]
    assert urgent.needs_urgent_review(evidence) is True


def test_no_escalation_after_all_targets_enter_real_money_validation():
    assert urgent.needs_urgent_review(_base_evidence()) is False


def test_watch_source_version_is_day_scoped_and_force_is_hour_scoped():
    a = urgent._finding_source_version(1787533200, force=True)
    b = urgent._finding_source_version(1787536799, force=True)
    assert a == b
    assert a.startswith("urgent-")

    c = urgent._finding_source_version(1787533200, force=False)
    d = urgent._finding_source_version(1787536800, force=False)
    assert c == d
    assert c.startswith("watch-")


def test_target_strategy_set_is_complete():
    assert set(urgent.TARGET_STRATEGIES) == {
        "Cross Venue Net Arbitrage",
        "Liquidity Confirmed Momentum",
        "Dislocation Mean Reversion",
        "Flow Acceleration",
        "New Liquidity Quality",
        "Learned Route Replication",
        "Forecasted Positive Net Edge",
    }
