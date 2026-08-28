from __future__ import annotations

from pathlib import Path

from grok_known_assets_bot.control import default_state, is_armed, load_state, save_state


def test_default_state_is_fail_closed(tmp_path: Path):
    path = tmp_path / "grok_control.json"
    state = load_state(path)
    assert state == default_state()
    assert is_armed(path) is False
    assert state["mode"] == "PAPER_ONLY"
    assert state["live_money_enabled"] is False


def test_arm_state_is_paper_only(tmp_path: Path):
    path = tmp_path / "grok_control.json"
    state = save_state(armed=True, updated_by="123", path=path)
    assert state["armed"] is True
    assert state["mode"] == "PAPER_ONLY"
    assert state["live_money_enabled"] is False

    loaded = load_state(path)
    assert loaded["armed"] is True
    assert loaded["mode"] == "PAPER_ONLY"
    assert loaded["live_money_enabled"] is False


def test_malformed_state_fails_closed(tmp_path: Path):
    path = tmp_path / "grok_control.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_state(path) == default_state()
    assert is_armed(path) is False
