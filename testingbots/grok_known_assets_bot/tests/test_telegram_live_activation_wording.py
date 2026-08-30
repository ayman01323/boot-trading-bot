from grok_known_assets_bot import telegram_control_runtime as runtime


def test_live_ready_reports_manual_canary_enabled(monkeypatch):
    monkeypatch.setattr(runtime._base, "load_state", lambda: {"live_money_enabled": True})
    text = runtime._event_alert_text("LIVE_READY", "solana:SOL:NATIVE", {})
    assert text is not None
    assert "Signing: ENABLED (manual canary)" in text
    assert "Broadcast path: ENABLED — APPROVAL-GATED; no transaction sent yet" in text
    assert "Signing: DISABLED" not in text


def test_live_ready_remains_disabled_when_canary_off(monkeypatch):
    monkeypatch.setattr(runtime._base, "load_state", lambda: {"live_money_enabled": False})
    text = runtime._event_alert_text("LIVE_READY", "solana:SOL:NATIVE", {})
    assert text is not None
    assert "Signing: DISABLED" in text
    assert "Broadcast: DISABLED" in text
