from decimal import Decimal
from types import SimpleNamespace

from learnerbot import sibot_profit_guard_patch as p
from learnerbot import telegram_sibot_quality_settings_patch as ui


def _good_metrics():
    return {
        "closed": 80,
        "wins": 52,
        "win_rate": Decimal("65"),
        "profit": Decimal("2"),
        "loss": Decimal("0.8"),
        "net": Decimal("1.2"),
        "profit_factor": Decimal("2.5"),
        "drawdown_pct": Decimal("8"),
        "avg_return_pct": Decimal("6"),
        "recent_closed": 20,
        "recent_win_rate": Decimal("70"),
        "recent_profit_factor": Decimal("2"),
        "recent_avg_return_pct": Decimal("7"),
        "history_complete": True,
    }


def _cfg():
    return {k: v[0] for k, v in p._QUALITY_DEFAULTS.items()} | {
        "require_complete_history": "true",
        "min_closed_trades": "50",
        "min_win_rate_pct": "55",
        "lookback_days": "60",
        "max_roundtrip_loss_pct": "2",
    }


def test_quality_gate_accepts_strong_leader_and_rejects_weak_profit_factor():
    cfg = _cfg()
    ok, reason = p._leader_quality_ok(_good_metrics(), cfg)
    assert ok is True and reason == "PASS"
    weak = _good_metrics()
    weak["profit_factor"] = Decimal("1.2")
    ok, reason = p._leader_quality_ok(weak, cfg)
    assert ok is False
    assert "profit factor" in reason


def test_quality_score_uses_recent_weight():
    cfg = _cfg()
    strong = p._quality_score(_good_metrics(), cfg)
    weaker = _good_metrics()
    weaker["recent_win_rate"] = Decimal("55")
    weaker["recent_profit_factor"] = Decimal("1.1")
    assert strong > p._quality_score(weaker, cfg)
    assert Decimal(0) <= strong <= Decimal(1)


def test_entry_guard_rejects_daily_loss_before_existing_execution_gate(monkeypatch):
    cfg = _cfg()
    trader = SimpleNamespace(telegram_id="1", chain=SimpleNamespace(chain_id=56))
    event = {"leader_wallet": "0x" + "1" * 40, "token": "0x" + "2" * 40}
    monkeypatch.setattr(p, "quality_metrics", lambda *a, **k: _good_metrics())
    monkeypatch.setattr(p, "_copied_metrics", lambda *a, **k: {"closed": 0, "win_rate": Decimal(0), "profit_factor": Decimal(0), "consecutive_losses": 0, "latest_closed_at": 0})
    monkeypatch.setattr(p, "_suspension_status", lambda *a, **k: (False, "PASS"))
    monkeypatch.setattr(p, "_chain_risk", lambda *a, **k: {"daily_pct": Decimal("-5"), "drawdown_pct": Decimal("2")})
    called = {"n": 0}
    def prev(*a, **k):
        called["n"] += 1
        return True, "PASS", {}
    monkeypatch.setattr(p, "_PREV_VALIDATE_ENTRY", prev)
    ok, reason, _ = p._validate_entry(None, trader, event, Decimal("1"), cfg, True)
    assert ok is False
    assert "daily chain loss" in reason
    assert called["n"] == 0


def test_entry_guard_requires_edge_to_cover_cost(monkeypatch):
    cfg = _cfg()
    trader = SimpleNamespace(telegram_id="1", chain=SimpleNamespace(chain_id=56))
    event = {"leader_wallet": "0x" + "1" * 40, "token": "0x" + "2" * 40}
    m = _good_metrics()
    m["avg_return_pct"] = Decimal("2")
    m["recent_avg_return_pct"] = Decimal("2")
    monkeypatch.setattr(p, "quality_metrics", lambda *a, **k: m)
    monkeypatch.setattr(p, "_copied_metrics", lambda *a, **k: {"closed": 0, "win_rate": Decimal(0), "profit_factor": Decimal(0), "consecutive_losses": 0, "latest_closed_at": 0})
    monkeypatch.setattr(p, "_suspension_status", lambda *a, **k: (False, "PASS"))
    monkeypatch.setattr(p, "_chain_risk", lambda *a, **k: {"daily_pct": Decimal(0), "drawdown_pct": Decimal(0)})
    monkeypatch.setattr(p._sibot, "_estimated_gas_native", lambda *a, **k: Decimal("0.0025"))
    monkeypatch.setattr(p, "_PREV_VALIDATE_ENTRY", lambda *a, **k: (True, "PASS", {"roundtrip_loss_pct": Decimal("1")}))
    ok, reason, check = p._validate_entry(None, trader, event, Decimal("1"), cfg, True)
    assert ok is False
    assert "does not cover" in reason
    assert check["expected_edge_pct"] == Decimal("2")


def test_quality_settings_keyboard_exposes_new_guards(monkeypatch):
    cfg = {k: v[0] for k, v in p._QUALITY_DEFAULTS.items()}
    cfg.update({
        "lookback_days": "60", "leaders_per_chain": "3", "allocation_pct": "15", "max_exposure_pct": "60",
        "min_closed_trades": "50", "min_win_rate_pct": "55", "max_signal_age_seconds": "20",
        "max_entry_deterioration_pct": "1.5", "max_roundtrip_loss_pct": "2", "stop_loss_pct": "10",
        "take_profit_pct": "25", "break_even_trigger_pct": "5", "break_even_floor_pct": "0.25",
        "trailing_trigger_pct": "10", "trailing_gap_pct": "4", "leader_exit_loss_cap_pct": "2",
        "min_exit_profit_pct": "0.10", "max_positions_per_chain": "5", "max_hold_hours": "24",
        "mirror_partial_sells": "true", "require_complete_history": "true",
    })
    monkeypatch.setattr(ui._sibot, "user_settings", lambda *a, **k: cfg)
    kb = ui.settings_keyboard(object(), "1")
    callbacks = [b["callback_data"] for row in kb["inline_keyboard"] for b in row if b.get("callback_data", "").startswith("sibot:set:")]
    assert "sibot:set:min_profit_factor" in callbacks
    assert "sibot:set:daily_loss_limit_pct" in callbacks
    assert "sibot:set:dynamic_max_allocation_pct" in callbacks
    assert "sibot:set:edge_cost_multiple" in callbacks
