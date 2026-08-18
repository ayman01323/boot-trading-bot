from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def test_balanced_frequency_restores_pre_tightening_style_without_live_gate_changes():
    path = ROOT / "learnerbot" / "solana_frequency_settings_migration.py"
    targets = _literal_assignment(path, "TARGETS")

    assert targets["leaders_per_user"] == "5"
    assert targets["min_closed_trades"] == "5"
    assert targets["min_win_rate_pct"] == "50"
    assert targets["min_profit_factor"] == "1.20"
    assert targets["max_signal_age_seconds"] == "30"
    assert targets["max_roundtrip_loss_pct"] == "3"
    assert targets["max_entry_deterioration_pct"] == "2"
    assert targets["candidate_limit"] == "150"
    assert targets["leader_poll_seconds"] == "4"

    # The frequency preset must not arm LIVE, change signing authority, or change
    # the separately user-requested 0.0005 SOL / 0.01 SOL wallet-capital controls.
    forbidden = {
        "solana_live_enabled",
        "live_trading_enabled",
        "auto_trading_enabled",
        "recommendation_mode",
        "live_trade_sol",
        "live_min_sol_reserve",
        "private_key",
        "signing_ready",
    }
    assert forbidden.isdisjoint(targets)


def test_balanced_frequency_keeps_exit_protection():
    targets = _literal_assignment(ROOT / "learnerbot" / "solana_frequency_settings_migration.py", "TARGETS")
    assert targets["stop_loss_pct"] == "10"
    assert targets["take_profit_pct"] == "25"
    assert targets["break_even_trigger_pct"] == "5"
    assert targets["break_even_floor_pct"] == "0.25"
    assert targets["trailing_trigger_pct"] == "10"
    assert targets["trailing_gap_pct"] == "4"
    assert targets["leader_exit_loss_cap_pct"] == "2"
    assert targets["mirror_partial_sells"] == "true"


def test_diagnostics_patch_records_reasons_and_exposes_activity_summary():
    source = (ROOT / "learnerbot" / "solana_trade_diagnostics_patch.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS live_decisions" in source
    assert "_sol.process_leader_event = process_leader_event" in source
    assert "SOLANA ACTIVITY — LAST 24H" in source
    for decision in ("BUY", "SELL", "REJECT", "SKIP"):
        assert decision in source
    assert "reason" in source
