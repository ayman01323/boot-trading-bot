from types import SimpleNamespace

from learnerbot import telegram_676_full_live_migration as migration


def test_missing_marker_does_not_reapply_legacy_live_settings_without_explicit_opt_in(monkeypatch, tmp_path):
    app = SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")
    app.csv_dir.mkdir(parents=True, exist_ok=True)
    app.data_dir.mkdir(parents=True, exist_ok=True)

    calls = []
    monkeypatch.setattr(migration, "_PREV_APP", lambda: app)
    monkeypatch.setattr(migration, "_arm_full_live", lambda current: calls.append(current))
    monkeypatch.delenv(migration.LEGACY_REAPPLY_ENV, raising=False)

    assert not (app.data_dir / migration.MARKER).exists()
    result = migration._app_with_676_full_live()

    assert result is app
    assert calls == []


def test_explicit_recovery_flag_can_still_run_historical_helper(monkeypatch, tmp_path):
    app = SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")
    app.csv_dir.mkdir(parents=True, exist_ok=True)
    app.data_dir.mkdir(parents=True, exist_ok=True)

    calls = []
    monkeypatch.setattr(migration, "_PREV_APP", lambda: app)
    monkeypatch.setattr(migration, "_arm_full_live", lambda current: calls.append(current))
    monkeypatch.setenv(migration.LEGACY_REAPPLY_ENV, "true")

    result = migration._app_with_676_full_live()

    assert result is app
    assert calls == [app]
