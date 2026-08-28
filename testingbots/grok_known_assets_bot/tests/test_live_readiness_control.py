from __future__ import annotations

import json
from pathlib import Path

from grok_known_assets_bot.telegram_control import _event_alert_text, handle_command
from grok_known_assets_bot.telegram_control_runtime import _entry_wording


def test_livecheck_command_arms_readiness_not_money(tmp_path: Path, monkeypatch):
    control = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(control))
    monkeypatch.setenv("GROK_JOURNAL_DB", str(tmp_path / "missing.sqlite3"))

    reply = handle_command("/groklivecheck on CONFIRM", "123")
    assert reply is not None and "LIVE READINESS ARMED" in reply
    state = json.loads(control.read_text(encoding="utf-8"))
    assert state["armed"] is True
    assert state["mode"] == "LIVE_READINESS"
    assert state["live_readiness_enabled"] is True
    assert state["live_money_enabled"] is False

    status = handle_command("/grokstatus", "123")
    assert status is not None
    assert "LIVE READINESS" in status
    assert "Real-money signing: DISABLED" in status
    assert "Transaction broadcast: DISABLED" in status


def test_runtime_wording_uses_normal_009_entry_not_canary():
    status = _entry_wording("Canary target: 0.0005 SOL (hard max 0.001 SOL)")
    assert status == "Entry target: 0.009 SOL (hard max 0.009 SOL)"
    ticket = _entry_wording("USDC→SOL canary: $1.00 → 0.009000000 SOL")
    assert ticket == "USDC→SOL entry: $1.00 → 0.009000000 SOL"


def test_live_ready_alert_is_explicitly_non_broadcasting():
    text = _event_alert_text(
        "LIVE_READY",
        "solana:SOL:NATIVE",
        {
            "reason": "LIVE_ROUTE_PREFLIGHT_PASS",
            "research_confidence": 0.693,
            "estimated_spend_usdc": 0.9,
            "quoted_sol_out": 0.009,
            "reverse_recovery_usdc": 0.882,
            "roundtrip_loss_pct": 2.0,
            "entry_impact_bps": 10.0,
            "reverse_impact_bps": 15.0,
            "stress_impact_bps": 30.0,
            "slippage_bps": 50,
            "expires_epoch": 1234,
        },
    )
    assert text is not None
    assert "LIVE-READY" in text
    assert "PREFLIGHT PASS" in text
    assert "Signing: DISABLED" in text
    assert "Broadcast: DISABLED" in text
