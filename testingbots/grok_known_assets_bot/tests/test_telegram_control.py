from __future__ import annotations

import json
from pathlib import Path

from grok_known_assets_bot.telegram_control import TelegramSettings, handle_command


def test_settings_require_dedicated_grok_token(monkeypatch):
    monkeypatch.delenv("GROK_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "shared-token-must-not-be-used")
    monkeypatch.setenv("GROK_TELEGRAM_CHAT_IDS", "123")
    try:
        TelegramSettings.from_env()
    except SystemExit as exc:
        assert "GROK_TELEGRAM_BOT_TOKEN" in str(exc)
    else:
        raise AssertionError("shared TELEGRAM_BOT_TOKEN must never be accepted")


def test_grok_commands_are_paper_only(tmp_path: Path, monkeypatch):
    control = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(control))

    status = handle_command("/grokstatus", "123")
    assert status is not None and "PAPER" in status and "DISABLED" in status

    armed = handle_command("/grokarm on CONFIRM", "123")
    assert armed is not None and "PAPER ARMED" in armed
    state = json.loads(control.read_text(encoding="utf-8"))
    assert state["armed"] is True
    assert state["mode"] == "PAPER_ONLY"
    assert state["live_money_enabled"] is False

    bad = handle_command("/grokarm on WRONG", "123")
    assert bad is not None and "Use exactly" in bad

    off = handle_command("/grokarm off", "123")
    assert off is not None and "OFF" in off
    state = json.loads(control.read_text(encoding="utf-8"))
    assert state["armed"] is False
    assert state["live_money_enabled"] is False


def test_non_grok_commands_are_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GROK_CONTROL_FILE", str(tmp_path / "grok_control.json"))
    assert handle_command("/status", "123") is None
    assert handle_command("hello", "123") is None
