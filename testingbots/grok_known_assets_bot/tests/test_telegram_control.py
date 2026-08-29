from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import grok_known_assets_bot.telegram_control as telegram_control
from grok_known_assets_bot.telegram_control import (
    TelegramApiError,
    TelegramSettings,
    _deliver_alert,
    _event_alert_text,
    _mark_alert_sent,
    _read_alert_events,
    _should_send_alert,
    handle_command,
)


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
    monkeypatch.setenv("GROK_JOURNAL_DB", str(tmp_path / "missing.sqlite3"))

    status = handle_command("/grokstatus", "123")
    assert status is not None and "PAPER" in status and "DISABLED" in status
    assert "Latest decision" in status

    armed = handle_command("/grokarm on CONFIRM", "123")
    assert armed is not None and "PAPER ARMED" in armed and "alerts are enabled" in armed
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


def _make_journal(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, asset_key TEXT, payload TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO events(ts, kind, asset_key, payload) VALUES(?,?,?,?)",
            (1.0, "REJECT", "solana:SOL:NATIVE", json.dumps({"reason": "GROK_RESEARCH_REJECT:test", "research_confidence": 0.71})),
        )
        db.execute(
            "INSERT INTO events(ts, kind, asset_key, payload) VALUES(?,?,?,?)",
            (2.0, "OPEN", "solana:SOL:NATIVE", json.dumps({"trade_id": "abc", "size_usd": 12.5, "entry_price": 201.25, "paper": True})),
        )
        db.commit()


def test_alert_events_are_read_and_formatted(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"
    _make_journal(db_path)
    events = _read_alert_events(db_path, 0)
    assert [row[2] for row in events] == ["REJECT", "OPEN"]
    reject_text = _event_alert_text(events[0][2], events[0][3], events[0][4])
    open_text = _event_alert_text(events[1][2], events[1][3], events[1][4])
    assert reject_text is not None and "REFUSED OPPORTUNITY" in reject_text
    assert "GROK_RESEARCH_REJECT:test" in reject_text
    assert open_text is not None and "PAPER TRADE OPENED" in open_text
    assert "$12.50" in open_text


def test_status_shows_latest_decision(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "state.sqlite3"
    _make_journal(db_path)
    monkeypatch.setenv("GROK_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("GROK_CONTROL_FILE", str(tmp_path / "grok_control.json"))
    status = handle_command("/grokstatus", "123")
    assert status is not None
    assert "Latest decision: OPEN solana:SOL:NATIVE" in status
    assert "Persisted PAPER positions: 0" in status


def test_repeated_rejects_are_deduplicated_after_success():
    sent_at: dict[str, float] = {}
    payload = {"reason": "WEAK_5M_MOMENTUM", "action": "REJECT"}
    assert _should_send_alert("DECISION", "solana:SOL:NATIVE", payload, sent_at, now=1000.0, repeat_seconds=300.0)
    _mark_alert_sent("DECISION", "solana:SOL:NATIVE", payload, sent_at, now=1000.0)
    assert not _should_send_alert("DECISION", "solana:SOL:NATIVE", payload, sent_at, now=1100.0, repeat_seconds=300.0)
    assert _should_send_alert("DECISION", "solana:SOL:NATIVE", payload, sent_at, now=1301.0, repeat_seconds=300.0)
    assert _should_send_alert("OPEN", "solana:SOL:NATIVE", {"trade_id": "x"}, sent_at, now=1302.0, repeat_seconds=300.0)


def test_canary_enable_requires_arm_and_readiness(tmp_path: Path, monkeypatch):
    cf = tmp_path / "grok_control.json"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(cf))
    monkeypatch.setenv("GROK_JOURNAL_DB", str(tmp_path / "state.sqlite3"))

    blocked = handle_command("/groklivecanary on CONFIRM", "123", "123")
    assert blocked is not None and "Arm the bot" in blocked
    state = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {}
    assert not state.get("live_canary_enabled")

    handle_command("/groklivecheck on CONFIRM", "123", "123")
    ok = handle_command("/groklivecanary on CONFIRM", "123", "123")
    assert ok is not None and "LIVE CANARY ENABLED" in ok
    state = json.loads(cf.read_text(encoding="utf-8"))
    assert state["live_canary_enabled"] is True
    assert state["live_money_enabled"] is True


def test_grokstop_disables_canary_and_cancels_tickets(tmp_path: Path, monkeypatch):
    from grok_known_assets_bot import live_canary as lc
    from grok_known_assets_bot.core import Journal

    cf = tmp_path / "grok_control.json"
    db_path = tmp_path / "state.sqlite3"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(cf))
    monkeypatch.setenv("GROK_JOURNAL_DB", str(db_path))
    j = Journal(str(db_path))
    lc.ensure_schema(j.db)
    lc.create_pending_entry(
        j.db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence={},
    )
    j.db.close()
    handle_command("/groklivecheck on CONFIRM", "123", "123")
    handle_command("/groklivecanary on CONFIRM", "123", "123")
    reply = handle_command("/grokstop", "123", "123")
    assert reply is not None and "Cancelled 1" in reply
    state = json.loads(cf.read_text(encoding="utf-8"))
    assert state["live_canary_enabled"] is False and state["armed"] is False
    j2 = Journal(str(db_path))
    lc.ensure_schema(j2.db)
    assert lc.list_pending(j2.db) == []
    j2.db.close()


def test_grokapprove_requires_authorised_user(tmp_path: Path, monkeypatch):
    from grok_known_assets_bot import live_canary as lc
    from grok_known_assets_bot.core import Journal

    db_path = tmp_path / "state.sqlite3"
    monkeypatch.setenv("GROK_CONTROL_FILE", str(tmp_path / "grok_control.json"))
    monkeypatch.setenv("GROK_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("GROK_LIVE_CANARY_APPROVER_USER_IDS", "999")
    j = Journal(str(db_path))
    lc.ensure_schema(j.db)
    aid = lc.create_pending_entry(
        j.db, asset_key="a", mint="M", input_micro_usdc=10, target_lamports=lc.TARGET_LAMPORTS,
        min_out_lamports=1, slippage_bps=50, evidence={},
    )
    j.db.close()
    denied = handle_command(f"/grokapprove {aid} CONFIRM", "123", "123")
    assert denied is not None and "Not authorised" in denied
    j2 = Journal(str(db_path))
    lc.ensure_schema(j2.db)
    assert lc._row(j2.db, aid)["status"] == lc.STATUS_PENDING
    j2.db.close()

    ok = handle_command(f"/grokapprove {aid} CONFIRM", "123", "999")
    assert ok is not None and "Approved" in ok
    j3 = Journal(str(db_path))
    lc.ensure_schema(j3.db)
    assert lc._row(j3.db, aid)["status"] == lc.STATUS_APPROVED
    j3.db.close()


def test_bad_chat_does_not_block_good_chat(monkeypatch):
    settings = TelegramSettings(token="test-token", chat_ids=frozenset({"bad", "good"}), poll_timeout_seconds=8)
    delivered_to: list[str] = []

    def fake_send(_settings: TelegramSettings, chat_id: str, _text: str) -> None:
        if chat_id == "bad":
            raise TelegramApiError(400, "sendMessage")
        delivered_to.append(chat_id)

    monkeypatch.setattr(telegram_control, "_send_message", fake_send)
    delivered, failed = _deliver_alert(settings, settings.chat_ids, "test")
    assert delivered == 1
    assert failed == 1
    assert delivered_to == ["good"]
