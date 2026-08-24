import csv
from pathlib import Path

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
