from __future__ import annotations

import json
import zipfile
from datetime import date

import pytest

from learnerbot import daily_botbuc_backup_patch as backup


def _make_source(tmp_path):
    source = tmp_path / "multichain-learning-bot-v2.2-fast-direct-market"
    source.mkdir()
    (source / "CSVbot").mkdir()
    (source / "CSVbot" / "settings.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "state.sqlite3").write_bytes(b"sqlite-bytes")
    (source / "empty").mkdir()
    return source


def test_daily_zip_upload_marker_and_two_hour_cleanup_schedule(tmp_path, monkeypatch):
    source = _make_source(tmp_path)
    out = tmp_path / "BotBuc"
    monkeypatch.setattr(backup, "SOURCE", source)
    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "DRIVE_DEST", "ndrive:BotBuc")

    monkeypatch.setattr(backup, "_remote_copy_matches", lambda path: False)
    uploaded = []
    monkeypatch.setattr(backup, "upload_to_drive", lambda path: uploaded.append(path.name) or True)
    scheduled = []
    monkeypatch.setattr(
        backup,
        "_schedule_local_delete",
        lambda path, verified_at: scheduled.append((path.name, verified_at)),
    )
    monkeypatch.setattr(backup.time, "time", lambda: 1_787_151_000.0)

    result = backup.run_daily_backup(date(2026, 8, 19))

    assert result == out / "2026-08-19.zip"
    assert result.is_file()
    assert uploaded == ["2026-08-19.zip"]
    assert scheduled == [("2026-08-19.zip", 1_787_151_000.0)]

    marker = out / ".2026-08-19.drive-verified.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["remote"] == "ndrive:BotBuc/2026-08-19.zip"
    assert payload["size"] == result.stat().st_size
    assert payload["verified_at"] == 1_787_151_000.0

    with zipfile.ZipFile(result) as zf:
        names = set(zf.namelist())
    prefix = source.name + "/"
    assert prefix in names
    assert prefix + "CSVbot/settings.csv" in names
    assert prefix + "data/state.sqlite3" in names
    assert prefix + "empty/" in names


def test_expired_verified_local_zip_is_deleted_without_recreating_same_day(tmp_path, monkeypatch):
    out = tmp_path / "BotBuc"
    out.mkdir()
    target = out / "2026-08-19.zip"
    target.write_bytes(b"archive")
    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "DRIVE_DEST", "ndrive:BotBuc")

    marker = out / ".2026-08-19.drive-verified.json"
    marker.write_text(
        json.dumps(
            {
                "remote": "ndrive:BotBuc/2026-08-19.zip",
                "size": len(b"archive"),
                "verified_at": 10_000.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backup.time, "time", lambda: 10_000.0 + 2 * 60 * 60 + 1)

    monkeypatch.setattr(
        backup,
        "_build_zip",
        lambda path: pytest.fail("same-day verified backup must not be rebuilt"),
    )
    monkeypatch.setattr(
        backup,
        "upload_to_drive",
        lambda path: pytest.fail("same-day verified backup must not be re-uploaded"),
    )

    result = backup.run_daily_backup(date(2026, 8, 19))

    assert result == target
    assert not target.exists()
    assert marker.exists()


def test_failed_upload_retains_only_local_zip_for_retry(tmp_path, monkeypatch):
    source = _make_source(tmp_path)
    out = tmp_path / "BotBuc"
    monkeypatch.setattr(backup, "SOURCE", source)
    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "DRIVE_DEST", "ndrive:BotBuc")
    monkeypatch.setattr(backup, "_remote_copy_matches", lambda path: False)
    monkeypatch.setattr(backup, "upload_to_drive", lambda path: False)

    with pytest.raises(RuntimeError, match="local ZIP retained"):
        backup.run_daily_backup(date(2026, 8, 19))

    target = out / "2026-08-19.zip"
    assert target.exists()
    assert len(list(out.glob("*.zip"))) == 1
    assert not (out / ".2026-08-19.drive-verified.json").exists()


def test_older_failed_zip_blocks_new_zip_until_drive_recovers(tmp_path, monkeypatch):
    source = _make_source(tmp_path)
    out = tmp_path / "BotBuc"
    out.mkdir()
    old = out / "2026-08-18.zip"
    old.write_bytes(b"old")
    monkeypatch.setattr(backup, "SOURCE", source)
    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "DRIVE_DEST", "ndrive:BotBuc")
    monkeypatch.setattr(backup, "_remote_copy_matches", lambda path: False)
    monkeypatch.setattr(backup, "upload_to_drive", lambda path: False)

    with pytest.raises(RuntimeError, match="refusing to create another ZIP"):
        backup.run_daily_backup(date(2026, 8, 19))

    assert old.exists()
    assert not (out / "2026-08-19.zip").exists()
    assert len(list(out.glob("*.zip"))) == 1


def test_cleanup_never_deletes_unverified_or_manual_zip(tmp_path, monkeypatch):
    out = tmp_path / "BotBuc"
    out.mkdir()
    manual = out / "manual.zip"
    manual.write_bytes(b"manual")
    unverified = out / "2026-08-18.zip"
    unverified.write_bytes(b"old")
    verified = out / "2026-08-17.zip"
    verified.write_bytes(b"verified")

    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "DRIVE_DEST", "ndrive:BotBuc")
    (out / ".2026-08-17.drive-verified.json").write_text(
        json.dumps(
            {
                "remote": "ndrive:BotBuc/2026-08-17.zip",
                "size": len(b"verified"),
                "verified_at": 1000.0,
            }
        ),
        encoding="utf-8",
    )

    removed = backup.cleanup_local_backups(now=1000.0 + 2 * 60 * 60 + 1)

    assert [p.name for p in removed] == ["2026-08-17.zip"]
    assert manual.exists()
    assert unverified.exists()


def test_remote_name_is_ndrive():
    assert backup.DRIVE_DEST == "ndrive:BotBuc"
    assert backup._remote_prefix() == "ndrive:"
