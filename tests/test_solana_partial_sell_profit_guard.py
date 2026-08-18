from types import SimpleNamespace

from learnerbot import solana_partial_sell_profit_guard_patch as guard


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        return _Rows(self.rows)

    def close(self):
        pass


def _app_and_position(entry_cost_sol="0.003"):
    app = SimpleNamespace(csv_dir="csv", data_dir="data")
    position = {
        "position_id": "p1",
        "telegram_id": "123",
        "leader_wallet": "leader",
        "mint": "mint",
        "status": "OPEN",
        "mode": "LIVE",
        "entry_cost_sol": entry_cost_sol,
    }
    return app, position


def _event(percent=25):
    return {
        "action": "SELL",
        "leader_wallet": "leader",
        "mint": "mint",
        "signature": f"leader-partial-{percent}",
        "sell_pct": percent,
    }


def _wire_common(monkeypatch, app, position, cfg=None, rent="0"):
    config = {
        "mirror_partial_sells": "true",
        "live_min_partial_exit_net_pct": "3.0",
        "live_min_position_economic_value_for_partial_sell_sol": "0.002",
        "live_max_fee_ratio_pct": "1.2",
    }
    config.update(cfg or {})
    monkeypatch.setattr(guard._sol, "settings", lambda app: dict(config))
    monkeypatch.setattr(guard._live, "all_users", lambda csv_dir, enabled_only=True: [{"telegram_id": "123"}])
    monkeypatch.setattr(guard._live, "live_enabled", lambda app, tid: True)
    monkeypatch.setattr(guard._sol._sibot, "user_settings", lambda app, tid, chain_id: {"enabled": "true"})
    monkeypatch.setattr(guard._sol, "_leader_rank", lambda app, tid, wallet: 1)
    monkeypatch.setattr(guard._sol, "connect", lambda app: _Conn([position]))
    monkeypatch.setattr(guard._rent, "_rent_principal_sol", lambda app, pid: guard.Decimal(str(rent)))


def test_minimum_partial_value_covers_base_fee_ratio():
    assert guard.minimum_economic_partial_value_lamports({"live_max_fee_ratio_pct": "1.2"}) == 416_667


def test_low_capital_canary_partial_is_held_before_valuation_or_execution(monkeypatch):
    # Cash cost includes refundable rent: 0.00253928 - 0.00203928 = 0.0005 SOL economic position.
    app, position = _app_and_position("0.00253928")
    _wire_common(monkeypatch, app, position, rent="0.00203928")
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("low-capital partial must not be valued for execution")),
    )
    monkeypatch.setattr(
        guard._live,
        "_claim_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("low-capital partial must not claim")),
    )
    monkeypatch.setattr(
        guard._live,
        "_close_live",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("low-capital partial must not execute")),
    )
    actions = guard.process_leader_event_partial_profit_guard(app, _event(90))
    assert actions[0]["action"] == "SKIP_PARTIAL_SELL"
    assert "low-capital HOLD" in actions[0]["reason"]


def test_negative_partial_sell_is_skipped_before_claim_or_execution(monkeypatch):
    app, position = _app_and_position()
    _wire_common(monkeypatch, app, position)
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda app, p, fraction: {"net_pct": "-0.50", "proceeds_sol": "0.00045"},
    )
    monkeypatch.setattr(
        guard._live,
        "_claim_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("negative partial must not claim")),
    )
    monkeypatch.setattr(
        guard._live,
        "_close_live",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("negative partial must not execute")),
    )

    actions = guard.process_leader_event_partial_profit_guard(app, _event(90))
    assert actions[0]["action"] == "SKIP_PARTIAL_SELL"
    assert "below required +3.0000%" in actions[0]["reason"]


def test_two_percent_partial_is_still_skipped_by_hard_three_percent_floor(monkeypatch):
    app, position = _app_and_position()
    _wire_common(monkeypatch, app, position, cfg={"live_min_partial_exit_net_pct": "1.0"})
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda app, p, fraction: {"net_pct": "2.00", "proceeds_sol": "0.001"},
    )
    monkeypatch.setattr(
        guard._live,
        "_claim_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("2% partial must not claim")),
    )
    actions = guard.process_leader_event_partial_profit_guard(app, _event(50))
    assert actions[0]["action"] == "SKIP_PARTIAL_SELL"
    assert "+3.0000%" in actions[0]["reason"]


def test_profitable_but_too_small_partial_is_skipped(monkeypatch):
    app, position = _app_and_position()
    _wire_common(monkeypatch, app, position)
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda app, p, fraction: {"net_pct": "4.00", "proceeds_sol": "0.00020"},
    )
    monkeypatch.setattr(
        guard._live,
        "_claim_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uneconomic partial must not claim")),
    )
    monkeypatch.setattr(
        guard._live,
        "_close_live",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uneconomic partial must not execute")),
    )

    actions = guard.process_leader_event_partial_profit_guard(app, _event(40))
    assert actions[0]["action"] == "SKIP_PARTIAL_SELL"
    assert "too small" in actions[0]["reason"]
    assert actions[0]["estimated_proceeds_lamports"] == 200_000


def test_profitable_economic_partial_can_execute_for_larger_position(monkeypatch):
    app, position = _app_and_position()
    _wire_common(monkeypatch, app, position)
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda app, p, fraction: {"net_pct": "4.00", "proceeds_sol": "0.0010"},
    )
    monkeypatch.setattr(guard._live, "_claim_attempt", lambda app, tid, event: (True, "attempt-1"))

    calls = []

    def close_live(app, tid, p, fraction, reason):
        calls.append((tid, p["position_id"], str(fraction), reason))
        return {"signature": "our-partial-sell", "net_sol": "0.00001", "trade": {"signature": "our-partial-sell"}}

    monkeypatch.setattr(guard._live, "_close_live", close_live)
    updates = []
    monkeypatch.setattr(
        guard._live,
        "_update_attempt",
        lambda app, key, status, trade=None, error="": updates.append((key, status)),
    )

    actions = guard.process_leader_event_partial_profit_guard(app, _event(50))
    assert calls == [("123", "p1", "0.5", "SOLANA_LEADER_PARTIAL_SELL_PROFIT_GATED")]
    assert updates == [("attempt-1", "EXECUTED")]
    assert actions[0]["action"] == "SELL"
    assert actions[0]["signature"] == "our-partial-sell"
    assert actions[0]["posttrade_result"] == "PROFIT"


def test_full_leader_sell_still_delegates_to_immediate_risk_control(monkeypatch):
    app, _ = _app_and_position()
    expected = [{"action": "SELL", "reason": "full-immediate"}]
    monkeypatch.setattr(guard, "_PREV_PROCESS", lambda app, event: expected)
    monkeypatch.setattr(
        guard._sol,
        "evaluate_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full exit must not use partial profit gate")),
    )
    assert guard.process_leader_event_partial_profit_guard(app, _event(100)) is expected
