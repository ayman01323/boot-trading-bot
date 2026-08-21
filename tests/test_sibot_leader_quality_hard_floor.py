from pathlib import Path
from types import SimpleNamespace

from learnerbot import sibot_leader_quality_hard_floor_patch as floor_patch


def _app(tmp_path: Path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")


def test_weak_per_user_win_rate_override_is_raised_to_platform_floor(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {"min_win_rate_pct": "45", "min_profit_factor": "1.5"}

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "5923828381", 56)
    assert cfg["min_win_rate_pct"] == "55"


def test_stricter_per_user_override_is_left_alone(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {"min_win_rate_pct": "70"}

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 56)
    assert cfg["min_win_rate_pct"] == "70"


def test_missing_settings_still_get_the_platform_floor(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", lambda app_, tid, chain_id=0: {})
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 56)
    assert cfg["min_win_rate_pct"] == "55"
    assert cfg["min_profit_factor"] == "1.5"
    assert cfg["min_closed_trades"] == "50"
    assert cfg["min_recent_win_rate_pct"] == "55"
    assert cfg["min_recent_profit_factor"] == "1.10"
    assert cfg["min_copied_win_rate_pct"] == "50"
    assert cfg["min_copied_profit_factor"] == "1.50"
    assert cfg["leader_suspend_minutes"] == "1440"
    assert cfg["require_complete_history"] == "true"


def test_ceilings_cannot_be_raised_past_platform_default(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {
            "max_leader_drawdown_pct": "80",
            "max_consecutive_copied_losses": "10",
            "min_copied_trades_for_guard": "1000",
        }

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 56)
    assert cfg["max_leader_drawdown_pct"] == "20"
    assert cfg["max_consecutive_copied_losses"] == "2"
    assert cfg["min_copied_trades_for_guard"] == "3"


def test_tighter_ceiling_override_is_left_alone(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {"max_leader_drawdown_pct": "5"}

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 56)
    assert cfg["max_leader_drawdown_pct"] == "5"


def test_require_complete_history_cannot_be_disabled_by_a_user(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {"require_complete_history": "false"}

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 56)
    assert cfg["require_complete_history"] == "true"


def test_other_keys_pass_through_untouched(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_prev(app_, tid, chain_id=0):
        return {"enabled": "true", "min_exit_profit_pct": ".25", "allocation_pct": "20"}

    monkeypatch.setattr(floor_patch, "_PREV_USER_SETTINGS", fake_prev)
    cfg = floor_patch.user_settings_with_quality_floor(app, "123", 0)
    assert cfg["enabled"] == "true"
    assert cfg["min_exit_profit_pct"] == ".25"
    assert cfg["allocation_pct"] == "20"
