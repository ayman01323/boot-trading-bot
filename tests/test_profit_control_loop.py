from __future__ import annotations

import sqlite3
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import profit_control_loop_patch as loop


def test_profiles_touch_only_bounded_entry_quality_keys():
    for name, overlay in loop.PROFILES.items():
        assert set(overlay).issubset(loop.CONTROLLED_KEYS), name
        assert not (set(overlay) & loop.FORBIDDEN_KEYS), name
    # Explicitly protect capital/execution safety controls from the optimiser.
    forbidden = {
        "live_trade_sol", "live_min_sol_reserve", "live_max_positions",
        "live_require_simulation", "live_no_output_disable_after", "solana_live_enabled",
    }
    for overlay in loop.PROFILES.values():
        assert forbidden.isdisjoint(overlay)


def test_success_requires_more_wins_positive_net_and_pf_above_110():
    assert loop._is_success(3, 2, Decimal("0.01"), Decimal("1.11"), min_trades=5, closed=5)
    assert not loop._is_success(2, 3, Decimal("0.01"), Decimal("2"), min_trades=5, closed=5)
    assert not loop._is_success(3, 2, Decimal("-0.01"), Decimal("2"), min_trades=5, closed=5)
    assert not loop._is_success(3, 2, Decimal("0.01"), Decimal("1.10"), min_trades=5, closed=5)
    assert not loop._is_success(2, 1, Decimal("0.01"), Decimal("2"), min_trades=5, closed=3)


def test_settings_overlay_changes_only_profile_keys(monkeypatch):
    baseline = {
        "live_trade_sol": "0.0005",
        "live_min_sol_reserve": "0.005",
        "live_require_simulation": "true",
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
        "min_win_rate_pct": "50",
        "min_profit_factor": "1.20",
    }
    monkeypatch.setattr(loop, "_PREV_SETTINGS", lambda app: dict(baseline))
    monkeypatch.setattr(loop, "active_profile", lambda app: "PROFIT_FIRST")
    cfg = loop.settings_with_profit_control(SimpleNamespace())
    assert cfg["max_signal_age_seconds"] == "25"
    assert cfg["max_roundtrip_loss_pct"] == "2.5"
    assert cfg["max_entry_deterioration_pct"] == "1.5"
    assert cfg["live_trade_sol"] == baseline["live_trade_sol"]
    assert cfg["live_min_sol_reserve"] == baseline["live_min_sol_reserve"]
    assert cfg["live_require_simulation"] == "true"


def test_losing_leader_is_blocked_but_successful_leader_passes(monkeypatch):
    monkeypatch.setattr(loop.time, "time", lambda: 1000)
    monkeypatch.setattr(loop, "_PREV_COPIED_OK", lambda app, tid, wallet, cfg: True)
    monkeypatch.setattr(loop, "_leader_control_row", lambda app, tid, wallet: {"blocked_until": 1200})
    assert loop.copied_ok_with_profit_control(None, "1", "leader", {}) is False
    monkeypatch.setattr(loop, "_leader_control_row", lambda app, tid, wallet: {"blocked_until": 900})
    assert loop.copied_ok_with_profit_control(None, "1", "leader", {}) is True


def test_profile_switch_uses_proven_winner_before_rotating():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(loop._SCHEMA)
    conn.execute(
        """INSERT INTO strategy_registry(profile,hours_observed,closed_trades,wins,losses,
           gross_profit_sol,gross_loss_sol,net_sol,profit_factor,successful,last_used_at,updated_at)
           VALUES('PROFIT_FIRST',4,8,5,3,'0.05','0.02','0.03','2.5',1,1,1)"""
    )
    current = {
        "profile": "BASELINE", "hours_observed": 3, "closed_trades": 8,
        "wins": 3, "losses": 5, "net_sol": "-0.02", "profit_factor": "0.7",
        "successful": False,
    }
    assert loop._choose_next_profile(conn, "BASELINE", current, 0) == "PROFIT_FIRST"


def test_hourly_wrapper_always_runs_deterministic_control(monkeypatch):
    monkeypatch.setattr(loop, "_ORIGINAL_GPT_REVIEW", lambda app, zip_path: {"ok": False, "error": "api down"})
    monkeypatch.setattr(loop, "run_profit_control_loop", lambda app, result: {
        "active_profile": "BASELINE", "live_armed_state_changed": False,
    })
    result = loop.run_hourly_gpt_review_with_control(object(), "audit.zip")
    assert result["ok"] is False
    assert result["control_loop"]["active_profile"] == "BASELINE"
    assert result["control_loop"]["live_armed_state_changed"] is False
