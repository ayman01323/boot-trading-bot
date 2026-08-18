from types import SimpleNamespace

from learnerbot import solana_execution_efficiency_patch as efficiency
from learnerbot import solana_profit_first_live_correction_patch as correction


def _loose_settings():
    return {
        "min_win_rate_pct": "50",
        "min_profit_factor": "1.1",
        "min_recent_win_rate_pct": "50",
        "min_recent_profit_factor": "1.0",
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
        "min_copied_trades_for_guard": "3",
        "min_copied_win_rate_pct": "40",
        "min_copied_profit_factor": "1.0",
        "max_consecutive_copied_losses": "3",
        "leader_suspend_minutes": "180",
        "break_even_trigger_pct": "5",
        "break_even_floor_pct": "0.10",
        "trailing_trigger_pct": "10",
        "trailing_gap_pct": "5",
        "take_profit_pct": "25",
        "stop_loss_pct": "10",
        "leader_exit_loss_cap_pct": "2.5",
        "live_max_total_fee_lamports": "100000",
        "live_max_fee_ratio_pct": "3",
        "live_expected_profit_margin_pct": "10",
        "live_max_fee_share_of_expected_profit_pct": "25",
        "live_enable_jito_tip": "true",
        "live_max_jito_tip_lamports": "1000",
        "live_order_slippage_bps": "50",
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
        "live_atomic_route_deterioration_bps": "50",
        "mirror_partial_sells": "true",
    }


def test_profit_first_policy_clamps_loose_runtime_settings(monkeypatch):
    monkeypatch.setattr(correction, "_PREV_SETTINGS", lambda app: _loose_settings())
    cfg = correction.settings_profit_first_live(SimpleNamespace())

    assert cfg["min_win_rate_pct"] == "60"
    assert cfg["min_profit_factor"] == "1.50"
    assert cfg["min_recent_win_rate_pct"] == "60"
    assert cfg["min_recent_profit_factor"] == "1.25"
    assert cfg["max_signal_age_seconds"] == "10"
    assert cfg["max_roundtrip_loss_pct"] == "1.0"
    assert cfg["max_entry_deterioration_pct"] == "0.25"

    assert cfg["min_copied_trades_for_guard"] == "2"
    assert cfg["min_copied_win_rate_pct"] == "50"
    assert cfg["min_copied_profit_factor"] == "1.20"
    assert cfg["max_consecutive_copied_losses"] == "2"
    assert cfg["leader_suspend_minutes"] == "360"

    assert cfg["break_even_trigger_pct"] == "3"
    assert cfg["break_even_floor_pct"] == "0.25"
    assert cfg["trailing_trigger_pct"] == "5"
    assert cfg["trailing_gap_pct"] == "2"
    assert cfg["take_profit_pct"] == "10"
    assert cfg["stop_loss_pct"] == "5"
    assert cfg["leader_exit_loss_cap_pct"] == "0"

    assert cfg["live_max_total_fee_lamports"] == "60000"
    assert cfg["live_max_fee_ratio_pct"] == "1.2"
    assert cfg["live_expected_profit_margin_pct"] == "6"
    assert cfg["live_max_fee_share_of_expected_profit_pct"] == "20"
    assert cfg["live_enable_jito_tip"] == "false"
    assert cfg["live_max_jito_tip_lamports"] == "0"
    assert cfg["live_order_slippage_bps"] == "30"
    assert cfg["live_max_combined_impact_slippage_bps"] == "100"
    assert cfg["live_multihop_max_combined_bps"] == "75"
    assert cfg["live_atomic_route_deterioration_bps"] == "25"
    assert cfg["live_require_price_impact_quote"] == "true"


def test_tiny_trade_total_fee_cap_is_6000_lamports(monkeypatch):
    monkeypatch.setattr(correction, "_PREV_SETTINGS", lambda app: _loose_settings())
    cfg = correction.settings_profit_first_live(SimpleNamespace())
    assert efficiency.dynamic_fee_cap_lamports(cfg, 500_000) == 6_000


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


def test_leader_sell_closes_immediately_without_waiting_for_loss_threshold(monkeypatch):
    app = SimpleNamespace(csv_dir="csv", data_dir="data")
    position = {
        "position_id": "p1",
        "telegram_id": "123",
        "leader_wallet": "leader",
        "mint": "mint",
        "status": "OPEN",
        "mode": "LIVE",
    }
    event = {
        "action": "SELL",
        "leader_wallet": "leader",
        "mint": "mint",
        "signature": "leader-sell-sig",
        "sell_pct": 100,
    }

    monkeypatch.setattr(correction._sol, "settings", lambda app: {"mirror_partial_sells": "true"})
    monkeypatch.setattr(correction._live, "all_users", lambda csv_dir, enabled_only=True: [{"telegram_id": "123"}])
    monkeypatch.setattr(correction._live, "live_enabled", lambda app, tid: True)
    monkeypatch.setattr(correction._sol._sibot, "user_settings", lambda app, tid, chain_id: {"enabled": "true"})
    monkeypatch.setattr(correction._sol, "_leader_rank", lambda app, tid, wallet: 1)
    monkeypatch.setattr(correction._sol, "connect", lambda app: _Conn([position]))
    monkeypatch.setattr(correction._live, "_claim_attempt", lambda app, tid, event: (True, "attempt-1"))

    # If the old decision path is used this test must fail: immediate leader exit
    # no longer waits for evaluate_position/min_exit_profit/stop-loss thresholds.
    monkeypatch.setattr(
        correction._sol,
        "evaluate_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not evaluate before leader SELL")),
    )

    calls = []

    def close_live(app, tid, p, fraction, reason):
        calls.append((tid, p["position_id"], str(fraction), reason))
        return {"signature": "our-sell-sig", "net_sol": "-0.000001", "trade": {"signature": "our-sell-sig"}}

    monkeypatch.setattr(correction._live, "_close_live", close_live)
    updates = []
    monkeypatch.setattr(
        correction._live,
        "_update_attempt",
        lambda app, key, status, trade=None, error="": updates.append((key, status)),
    )

    actions = correction.process_leader_event_profit_first(app, event)
    assert calls == [("123", "p1", "1", "SOLANA_LEADER_SELL_IMMEDIATE")]
    assert updates == [("attempt-1", "EXECUTED")]
    assert actions[0]["action"] == "SELL"
    assert actions[0]["signature"] == "our-sell-sig"
