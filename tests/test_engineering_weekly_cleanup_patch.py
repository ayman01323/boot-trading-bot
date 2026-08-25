from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from learnerbot import engineering_weekly_cleanup_patch as mod


def _old(path: Path, now: float, age: int) -> None:
    ts = now - age
    os.utime(path, (ts, ts))


def test_weekly_cleanup_is_allowlisted_and_preserves_trading_data(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")
    app.data_dir.mkdir()
    app.csv_dir.mkdir()

    roots = {
        "tmp": tmp_path / "tmp",
        "runner_diag": tmp_path / "diag",
        "runner_temp": tmp_path / "runner-temp",
        "pip_cache": tmp_path / "pip-cache",
        "backup": tmp_path / "BotBuc",
    }
    for root in roots.values():
        root.mkdir(parents=True)

    now = 2_000_000_000.0
    old_ci = roots["tmp"] / "deepseek-telegram-bridge-ci-123"
    old_ci.mkdir()
    (old_ci / "payload.txt").write_text("x" * 1024, encoding="utf-8")
    _old(old_ci, now, mod.STALE_TMP_SECONDS + 1)

    recent_ci = roots["tmp"] / "ai-mailbox-telegram-ci-456"
    recent_ci.mkdir()
    _old(recent_ci, now, 60)

    unrelated = roots["tmp"] / "important-unrelated"
    unrelated.mkdir()
    _old(unrelated, now, mod.STALE_TMP_SECONDS * 10)

    diag = roots["runner_diag"] / "Runner_20200101.log"
    diag.write_text("old", encoding="utf-8")
    _old(diag, now, mod.RUNNER_DIAG_RETENTION_SECONDS + 1)

    runner_temp = roots["runner_temp"] / "old.tmp"
    runner_temp.write_text("old", encoding="utf-8")
    _old(runner_temp, now, mod.STALE_TMP_SECONDS + 1)

    cache = roots["pip_cache"] / "wheels" / "old.whl"
    cache.parent.mkdir()
    cache.write_text("cache", encoding="utf-8")
    _old(cache, now, mod.PIP_CACHE_RETENTION_SECONDS + 1)

    incomplete = roots["backup"] / ".2026-08-20.zip.tmp"
    incomplete.write_text("partial", encoding="utf-8")
    _old(incomplete, now, mod.STALE_BACKUP_TMP_SECONDS + 1)
    complete = roots["backup"] / "2026-08-20.zip"
    complete.write_text("valid completed archive", encoding="utf-8")
    _old(complete, now, mod.STALE_BACKUP_TMP_SECONDS * 10)

    trading_db = app.data_dir / "base.sqlite3"
    trading_db.write_text("must stay", encoding="utf-8")
    trading_csv = app.csv_dir / "risk_settings.csv"
    trading_csv.write_text("must stay", encoding="utf-8")

    monkeypatch.setattr(mod, "_path_busy", lambda _path: False)
    monkeypatch.setattr(
        mod,
        "_disk",
        lambda _path="/": {
            "total_bytes": 20 * 1024**3,
            "used_bytes": 10 * 1024**3,
            "free_bytes": 10 * 1024**3,
            "used_pct": 50.0,
        },
    )

    result = mod.run_weekly_cleanup(app, now=now, roots=roots)

    assert result["status"] == "SUCCESS"
    assert not old_ci.exists()
    assert recent_ci.exists()
    assert unrelated.exists()
    assert not diag.exists()
    assert not runner_temp.exists()
    assert not cache.exists()
    assert not incomplete.exists()
    assert complete.exists()
    assert trading_db.exists()
    assert trading_csv.exists()
    assert result["safety"]["deletes_databases"] is False
    assert result["safety"]["deletes_csvbot"] is False


def test_backup_headroom_guard_refuses_before_builder(tmp_path, monkeypatch):
    target = tmp_path / "2026-08-25.zip"
    called = {"builder": False}

    class Usage:
        total = 20 * 1024**3
        used = 19 * 1024**3
        free = 1 * 1024**3

    monkeypatch.setattr(mod._backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(mod._backup, "_ensure_backup_dir", lambda: tmp_path.mkdir(exist_ok=True))
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda _path: Usage())

    def builder(_target):
        called["builder"] = True

    monkeypatch.setattr(mod, "_ORIGINAL_BUILD_ZIP", builder)

    with pytest.raises(RuntimeError, match="insufficient disk headroom"):
        mod._backup_build_with_headroom(target)

    assert called["builder"] is False
    assert not (tmp_path / ".2026-08-25.zip.tmp").exists()


def test_weekly_due_uses_latest_result_timestamp(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")
    app.data_dir.mkdir()
    app.csv_dir.mkdir()
    now = 2_000_000_000

    assert mod._due(app, now=now) is True
    mod._write_result(app, {"generated_epoch": now - mod.WEEK_SECONDS + 1})
    assert mod._due(app, now=now) is False
    mod._write_result(app, {"generated_epoch": now - mod.WEEK_SECONDS})
    assert mod._due(app, now=now) is True
