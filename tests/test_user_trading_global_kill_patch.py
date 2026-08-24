import csv
import threading
import time
from pathlib import Path

from learnerbot import user_trading_global_kill_patch as settings_patch
from learnerbot.user_registry import set_user_setting, user_bool, user_setting


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "CSVbot"
    p.mkdir()
    (p / "user_trading_settings.csv").write_text(
        "telegram_id,chain_id,setting,value,description\n",
        encoding="utf-8",
    )
    return p


def _rows(csv_dir: Path):
    with (csv_dir / "user_trading_settings.csv").open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_autotrade_off_ignores_stale_chain_specific_true(tmp_path):
    csv_dir = _csv(tmp_path)
    (csv_dir / "user_trading_settings.csv").write_text(
        "telegram_id,chain_id,setting,value,description\n"
        "123,*,auto_trading_enabled,false,global kill\n"
        "123,56,auto_trading_enabled,true,stale bsc override\n"
        "123,8453,auto_trading_enabled,true,stale base override\n",
        encoding="utf-8",
    )

    assert user_bool(csv_dir, 123, 0, "auto_trading_enabled", True) is False
    assert user_bool(csv_dir, 123, 56, "auto_trading_enabled", True) is False
    assert user_bool(csv_dir, 123, 8453, "auto_trading_enabled", True) is False


def test_global_autotrade_write_removes_duplicates_aliases_and_chain_rows(tmp_path):
    csv_dir = _csv(tmp_path)
    (csv_dir / "user_trading_settings.csv").write_text(
        "telegram_id,chain_id,setting,value,description\n"
        "123,*,auto_trading_enabled,true,old global\n"
        "123,*,auto_trading_enabled,true,duplicate global\n"
        "123,0,auto_trading_enabled,true,legacy alias\n"
        "123,56,auto_trading_enabled,true,legacy chain row\n"
        "123,56,auto_input_base,0.02,keep me\n",
        encoding="utf-8",
    )

    set_user_setting(csv_dir, 123, "auto_trading_enabled", "false")

    rows = _rows(csv_dir)
    switch_rows = [
        r for r in rows
        if r["telegram_id"] == "123" and r["setting"] == "auto_trading_enabled"
    ]
    assert switch_rows == [{
        "telegram_id": "123",
        "chain_id": "*",
        "setting": "auto_trading_enabled",
        "value": "false",
        "description": "",
    }]
    assert user_bool(csv_dir, 123, 56, "auto_trading_enabled", True) is False
    assert user_setting(csv_dir, 123, 56, "auto_input_base") == "0.02"


def test_autotrade_can_turn_back_on_after_global_off(tmp_path):
    csv_dir = _csv(tmp_path)
    set_user_setting(csv_dir, 123, "auto_trading_enabled", "false")
    assert user_bool(csv_dir, 123, 0, "auto_trading_enabled", True) is False

    set_user_setting(csv_dir, 123, "auto_trading_enabled", "true")
    assert user_bool(csv_dir, 123, 0, "auto_trading_enabled", False) is True
    rows = [
        r for r in _rows(csv_dir)
        if r["telegram_id"] == "123" and r["setting"] == "auto_trading_enabled"
    ]
    assert len(rows) == 1
    assert rows[0]["chain_id"] == "*"
    assert rows[0]["value"] == "true"


def test_legacy_or_chain_writes_cannot_override_canonical_global_controls(tmp_path):
    csv_dir = _csv(tmp_path)

    set_user_setting(csv_dir, 123, "live_trading_enabled", "false")
    set_user_setting(csv_dir, 123, "live_trading_enabled", "true", chain_id=56)
    set_user_setting(csv_dir, 123, "live_trading_enabled", "true", chain_id=0)
    assert user_bool(csv_dir, 123, 1, "live_trading_enabled", True) is False
    assert user_bool(csv_dir, 123, 56, "live_trading_enabled", True) is False

    set_user_setting(csv_dir, 123, "recommendation_mode", "SHADOW")
    set_user_setting(csv_dir, 123, "recommendation_mode", "ARMED", chain_id=56)
    assert user_setting(csv_dir, 123, 8453, "recommendation_mode") == "SHADOW"
    set_user_setting(csv_dir, 123, "recommendation_mode", "ARMED")
    assert user_setting(csv_dir, 123, 8453, "recommendation_mode") == "ARMED"


def test_numeric_policy_stays_chain_scoped(tmp_path):
    csv_dir = _csv(tmp_path)
    set_user_setting(csv_dir, 123, "auto_input_base", "0.005", chain_id="*")
    set_user_setting(csv_dir, 123, "auto_input_base", "0.02", chain_id=56)
    assert user_setting(csv_dir, 123, 56, "auto_input_base") == "0.02"
    assert user_setting(csv_dir, 123, 8453, "auto_input_base") == "0.005"


def test_concurrent_user_setting_writes_are_serialized(monkeypatch, tmp_path):
    csv_dir = _csv(tmp_path)
    original_atomic_write = settings_patch._ur._atomic_write
    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    call_guard = threading.Lock()
    call_count = {"value": 0}

    def delayed_atomic_write(path, rows, headers):
        with call_guard:
            call_count["value"] += 1
            call_number = call_count["value"]
        if call_number == 1:
            first_write_entered.set()
            assert release_first_write.wait(timeout=2)
        return original_atomic_write(path, rows, headers)

    monkeypatch.setattr(settings_patch._ur, "_atomic_write", delayed_atomic_write)
    errors = []

    def write_size():
        try:
            set_user_setting(csv_dir, 123, "auto_input_base", "0.005")
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def write_profit():
        try:
            set_user_setting(csv_dir, 123, "min_net_profit_base", "0.0002")
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    t1 = threading.Thread(target=write_size)
    t2 = threading.Thread(target=write_profit)
    t1.start()
    assert first_write_entered.wait(timeout=2)
    t2.start()

    # The second writer must be blocked by the settings lock until the complete
    # first read-modify-replace-readback sequence has finished.
    time.sleep(0.05)
    with call_guard:
        assert call_count["value"] == 1

    release_first_write.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert not t1.is_alive() and not t2.is_alive()
    assert errors == []

    assert user_setting(csv_dir, 123, 0, "auto_input_base") == "0.005"
    assert user_setting(csv_dir, 123, 0, "min_net_profit_base") == "0.0002"
