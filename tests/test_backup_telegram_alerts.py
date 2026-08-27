from __future__ import annotations

import csv
import json
from datetime import datetime
from types import SimpleNamespace

from learnerbot import backup_telegram_alert_patch as alerts


def _app(tmp_path):
    csv_dir = tmp_path / "CSVbot"
    csv_dir.mkdir()
    users = csv_dir / "users.csv"
    headers = [
        "telegram_id", "role", "status", "fee_plan_id", "label", "allowed_chains",
        "max_wallets", "can_transfer", "can_manual_trade", "can_auto_trade",
        "created_epoch", "activated_epoch", "notes",
    ]
    rows = [
        {"telegram_id": "1001", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "1002", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "2001", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "1003", "role": "MASTER", "status": "SUSPENDED"},
    ]
    with users.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in headers})
    return SimpleNamespace(csv_dir=csv_dir, telegram_bot_token="token")


def test_success_message_goes_only_to_active_masters_and_only_once(tmp_path, monkeypatch):
    app = _app(tmp_path)
    backup_dir = tmp_path / "BotBuc"
    backup_dir.mkdir()
    monkeypatch.setattr(alerts._backup, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(alerts._backup, "DRIVE_DEST", "ndrive:BotBuc")
    monkeypatch.setattr(alerts._backup, "LOCAL_RETENTION_HOURS", 2)
    verification = {"size": 123456, "verified_at": 1.0, "remote": "ndrive:BotBuc/2026-08-20.zip"}
    monkeypatch.setattr(alerts._backup, "_load_verification", lambda target: verification)

    calls = []

    def fake_send(token, chat_ids, text, **kwargs):
        calls.append((token, list(chat_ids), text, kwargs))
        return {
            "sent_chats": len(chat_ids),
            "failed_chats": 0,
            "details": [{"chat_id": x, "ok": True, "messages": 1} for x in chat_ids],
        }

    monkeypatch.setattr(alerts, "send_to_chats", fake_send)
    now = datetime(2026, 8, 20, 5, 0, 0)

    assert alerts.check_backup_alerts(app, now) == "success"
    assert calls[0][1] == ["1001", "1002"]
    assert "BACKUP SUCCESS" in calls[0][2]
    assert "ndrive:BotBuc/2026-08-20.zip" in calls[0][2]

    # The delivery marker prevents duplicate success messages to the same masters.
    assert alerts.check_backup_alerts(app, now) == "success"
    assert len(calls) == 1
    marker = backup_dir / ".2026-08-20.telegram-backup-success.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["delivered_master_ids"] == ["1001", "1002"]


def test_failure_alert_repeats_no_more_than_once_per_hour_until_verified(tmp_path, monkeypatch):
    # Production currently has backup failure alerts intentionally disabled.
    # This unit test exercises the alert cadence itself, so enable the feature
    # only for the duration of this test rather than changing production policy.
    monkeypatch.setattr(alerts, "FAILURE_ALERTS_ENABLED", True)

    app = _app(tmp_path)
    backup_dir = tmp_path / "BotBuc"
    backup_dir.mkdir()
    target = backup_dir / "2026-08-20.zip"
    target.write_bytes(b"local backup")
    monkeypatch.setattr(alerts._backup, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(alerts._backup, "DRIVE_DEST", "ndrive:BotBuc")
    monkeypatch.setattr(alerts._backup, "_load_verification", lambda path: None)

    calls = []

    def fake_send(token, chat_ids, text, **kwargs):
        calls.append((list(chat_ids), text))
        return {
            "sent_chats": len(chat_ids),
            "failed_chats": 0,
            "details": [{"chat_id": x, "ok": True, "messages": 1} for x in chat_ids],
        }

    monkeypatch.setattr(alerts, "send_to_chats", fake_send)

    assert alerts.check_backup_alerts(app, datetime(2026, 8, 20, 3, 59, 59)) == "waiting"
    assert calls == []

    assert alerts.check_backup_alerts(app, datetime(2026, 8, 20, 4, 0, 0)) == "failure-alert"
    assert len(calls) == 1
    assert calls[0][0] == ["1001", "1002"]
    assert "FAILURE / NOT VERIFIED" in calls[0][1]
    assert "every 1 hour" in calls[0][1]

    assert alerts.check_backup_alerts(app, datetime(2026, 8, 20, 4, 59, 59)) == "failure-wait"
    assert len(calls) == 1

    assert alerts.check_backup_alerts(app, datetime(2026, 8, 20, 5, 0, 0)) == "failure-alert"
    assert len(calls) == 2
