from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from learnerbot import backup_alert_hourly_reason_patch as harden


def _reset(tmp_path, monkeypatch):
    backup_dir = tmp_path / "BotBuc"
    backup_dir.mkdir()
    monkeypatch.setattr(harden._backup, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(harden._backup, "DRIVE_DEST", "ndrive:BotBuc")
    harden._LAST_SENT_BY_DAY.clear()
    harden._LAST_REASON = ""
    return backup_dir


def test_hourly_memory_throttle_blocks_one_minute_repeat_even_if_state_write_fails(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    day = date(2026, 8, 25)
    target = harden._backup.BACKUP_DIR / "2026-08-25.zip"
    calls = []

    def exploding_original(app, alert_day, alert_target, now_epoch):
        calls.append(float(now_epoch))
        raise OSError("simulated marker write failure")

    monkeypatch.setattr(harden, "_ORIG_SEND_FAILURE_ALERT", exploding_original)

    assert harden._failure_alert_due_hourly(day, 1000.0) is True
    assert harden._send_failure_alert_hourly(SimpleNamespace(), day, target, 1000.0) is False
    assert len(calls) == 1
    assert harden._failure_alert_due_hourly(day, 1060.0) is False
    assert harden._failure_alert_due_hourly(day, 4599.0) is False
    assert harden._failure_alert_due_hourly(day, 4600.0) is True


def test_failure_warning_includes_persisted_reason(tmp_path, monkeypatch):
    backup_dir = _reset(tmp_path, monkeypatch)
    day = date(2026, 8, 25)
    target = backup_dir / "2026-08-25.zip"
    target.write_bytes(b"backup")

    harden._write_status(
        day,
        "failure",
        "RuntimeError: Drive upload not verified; local ZIP retained for hourly retry",
        target,
    )
    text = harden._failure_text_with_reason(day, target)

    assert "Failure reason:" in text
    assert "Drive upload not verified" in text
    assert "Local ZIP: present and retained" in text
    assert "no more than once every 1 hour" in text


def test_backup_exception_is_saved_as_failure_reason(tmp_path, monkeypatch):
    backup_dir = _reset(tmp_path, monkeypatch)
    monkeypatch.setattr(
        harden,
        "_ORIG_RUN_DAILY_BACKUP",
        lambda today=None: (_ for _ in ()).throw(RuntimeError("remote verification failed")),
    )

    with pytest.raises(RuntimeError, match="remote verification failed"):
        harden._run_daily_backup_with_reason(date(2026, 8, 25))

    payload = json.loads((backup_dir / ".backup-last-status.json").read_text(encoding="utf-8"))
    assert payload["date"] == "2026-08-25"
    assert payload["status"] == "failure"
    assert "remote verification failed" in payload["reason"]


def test_diagnose_reports_rclone_configuration_failure_when_no_saved_reason(tmp_path, monkeypatch):
    backup_dir = _reset(tmp_path, monkeypatch)
    day = date(2026, 8, 25)
    target = backup_dir / "2026-08-25.zip"
    monkeypatch.setattr(harden._backup, "_rclone_available", lambda: (False, "rclone config missing"))

    assert harden._diagnose_failure(day, target) == "rclone config missing"
