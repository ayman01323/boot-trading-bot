from __future__ import annotations

import zipfile
from datetime import date

from learnerbot import daily_botbuc_backup_patch as backup


def test_daily_zip_name_full_tree_and_server_only_retention(tmp_path, monkeypatch):
    source = tmp_path / "multichain-learning-bot-v2.2-fast-direct-market"
    source.mkdir()
    (source / "CSVbot").mkdir()
    (source / "CSVbot" / "settings.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "state.sqlite3").write_bytes(b"sqlite-bytes")
    (source / "empty").mkdir()

    out = tmp_path / "BotBuc"
    monkeypatch.setattr(backup, "SOURCE", source)
    monkeypatch.setattr(backup, "BACKUP_DIR", out)

    uploaded = []
    monkeypatch.setattr(backup, "upload_to_drive", lambda path: uploaded.append(path.name) or True)

    # 31 days old relative to 2026-08-19 -> must be deleted locally.
    out.mkdir()
    (out / "2026-07-19.zip").write_bytes(b"old")
    # Exactly 30 days old -> retained.
    (out / "2026-07-20.zip").write_bytes(b"keep")

    result = backup.run_daily_backup(date(2026, 8, 19))

    assert result == out / "2026-08-19.zip"
    assert result.is_file()
    assert uploaded == ["2026-08-19.zip"]
    assert not (out / "2026-07-19.zip").exists()
    assert (out / "2026-07-20.zip").exists()

    with zipfile.ZipFile(result) as zf:
        names = set(zf.namelist())
    prefix = source.name + "/"
    assert prefix in names
    assert prefix + "CSVbot/settings.csv" in names
    assert prefix + "data/state.sqlite3" in names
    assert prefix + "empty/" in names


def test_cleanup_does_not_call_rclone_or_touch_non_date_zip(tmp_path, monkeypatch):
    out = tmp_path / "BotBuc"
    out.mkdir()
    (out / "manual.zip").write_bytes(b"manual")
    (out / "2026-07-01.zip").write_bytes(b"old")
    monkeypatch.setattr(backup, "BACKUP_DIR", out)

    removed = backup.cleanup_local_backups(date(2026, 8, 19))

    assert [p.name for p in removed] == ["2026-07-01.zip"]
    assert (out / "manual.zip").exists()
