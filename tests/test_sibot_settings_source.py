from types import SimpleNamespace

from learnerbot import sibot as sibot
from learnerbot import solana_live_patch as sol_live
from learnerbot import solana_sibot as solana
from learnerbot import telegram_sibot_settings_source_patch as source
from learnerbot.user_registry import USER_HEADERS, set_user_setting, update_user


def _app(tmp_path):
    csv_dir = tmp_path / "CSVbot"
    data_dir = tmp_path / "data"
    csv_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)


def _write_users(app):
    rows = [
        {
            "telegram_id": "111",
            "role": "MASTER",
            "status": "ACTIVE",
            "fee_plan_id": "MASTER",
            "label": "Main Master",
            "allowed_chains": "*",
            "max_wallets": "20",
            "can_transfer": "true",
            "can_manual_trade": "true",
            "can_auto_trade": "true",
            "created_epoch": "1",
            "activated_epoch": "1",
            "notes": "original master",
        },
        {
            "telegram_id": "222",
            "role": "MASTER",
            "status": "ACTIVE",
            "fee_plan_id": "MASTER",
            "label": "Added Master",
            "allowed_chains": "*",
            "max_wallets": "20",
            "can_transfer": "true",
            "can_manual_trade": "true",
            "can_auto_trade": "true",
            "created_epoch": "2",
            "activated_epoch": "2",
            "notes": "later master",
        },
        {
            "telegram_id": "333",
            "role": "USER",
            "status": "ACTIVE",
            "fee_plan_id": "STANDARD",
            "label": "User",
            "allowed_chains": "*",
            "max_wallets": "5",
            "can_transfer": "true",
            "can_manual_trade": "true",
            "can_auto_trade": "true",
            "created_epoch": "3",
            "activated_epoch": "3",
            "notes": "",
        },
    ]
    path = app.csv_dir / "users.csv"
    import csv

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USER_HEADERS)
        w.writeheader()
        w.writerows(rows)


def test_primary_master_is_first_master_not_later_added_master(tmp_path):
    app = _app(tmp_path)
    _write_users(app)
    assert source.primary_master_id(app.csv_dir) == "111"

    # A later-added active MASTER must never take over the source merely because
    # the original MASTER is temporarily inactive/suspended.
    update_user(app.csv_dir, "111", status="SUSPENDED")
    assert source.primary_master_id(app.csv_dir) == "111"


def test_production_original_master_identity_wins_even_if_csv_order_changes(tmp_path):
    app = _app(tmp_path)
    _write_users(app)
    # Re-purpose the first fixture MASTER as a later-added account, then append the
    # known original platform MASTER after it. The pinned identity must still win.
    import csv
    path = app.csv_dir / "users.csv"
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows.append({
        "telegram_id": source.ORIGINAL_MAIN_MASTER_ID,
        "role": "MASTER",
        "status": "ACTIVE",
        "fee_plan_id": "MASTER",
        "label": "Original Main Master",
        "allowed_chains": "*",
        "max_wallets": "20",
        "can_transfer": "true",
        "can_manual_trade": "true",
        "can_auto_trade": "true",
        "created_epoch": "0",
        "activated_epoch": "0",
        "notes": "production original",
    })
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USER_HEADERS)
        w.writeheader()
        w.writerows(rows)
    assert source.primary_master_id(app.csv_dir) == source.ORIGINAL_MAIN_MASTER_ID


def test_main_master_source_inherits_tuning_but_not_execution_authority(tmp_path):
    app = _app(tmp_path)
    _write_users(app)

    # Different tuning on each account makes accidental use of the later MASTER
    # or the user's stale override visible in the assertion.
    set_user_setting(app.csv_dir, "111", "sibot_allocation_pct", "11", chain_id="*")
    set_user_setting(app.csv_dir, "222", "sibot_allocation_pct", "22", chain_id="*")
    set_user_setting(app.csv_dir, "333", "sibot_allocation_pct", "33", chain_id="*")

    # Execution authority must remain local even while tuning follows Main Master.
    set_user_setting(app.csv_dir, "111", "sibot_enabled", "true", chain_id="*")
    set_user_setting(app.csv_dir, "111", "sibot_auto_trade_enabled", "true", chain_id="*")
    set_user_setting(app.csv_dir, "333", "sibot_enabled", "false", chain_id="*")
    set_user_setting(app.csv_dir, "333", "sibot_auto_trade_enabled", "false", chain_id="*")
    set_user_setting(app.csv_dir, "333", source.SOURCE_KEY, source.SOURCE_PRIMARY_MASTER, chain_id="*")

    cfg = sibot.user_settings(app, "333", 0)
    assert cfg["allocation_pct"] == "11"
    assert cfg["enabled"] == "false"
    assert cfg["auto_trade_enabled"] == "false"
    assert cfg["_settings_source"] == source.SOURCE_PRIMARY_MASTER
    assert cfg["_settings_source_tid"] == "111"


def test_main_master_source_undoes_stale_solana_size_reserve_without_inheriting_live_enable(tmp_path):
    app = _app(tmp_path)
    _write_users(app)

    set_user_setting(app.csv_dir, "111", "solana_live_trade_sol", "0.004", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "111", "solana_live_min_reserve_sol", "0.015", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "333", "solana_live_trade_sol", "0.0005", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "333", "solana_live_min_reserve_sol", "0.005", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "111", "solana_live_enabled", "true", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "333", "solana_live_enabled", "false", chain_id=solana.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, "333", source.SOURCE_KEY, source.SOURCE_PRIMARY_MASTER, chain_id="*")

    trade, reserve = sol_live.live_limits(
        app,
        "333",
        {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02"},
    )
    # Trade size is now fixed at 0.009 SOL regardless of settings source; only the
    # reserve is routed through the selected Main Master source.
    assert str(trade) == "0.009"
    assert str(reserve) == "0.015"
    assert sol_live.live_enabled(app, "333") is False


def test_switching_back_to_my_settings_restores_dormant_personal_tuning(tmp_path):
    app = _app(tmp_path)
    _write_users(app)
    set_user_setting(app.csv_dir, "111", "sibot_allocation_pct", "11", chain_id="*")
    set_user_setting(app.csv_dir, "333", "sibot_allocation_pct", "33", chain_id="*")
    set_user_setting(app.csv_dir, "333", source.SOURCE_KEY, source.SOURCE_PRIMARY_MASTER, chain_id="*")
    assert sibot.user_settings(app, "333", 0)["allocation_pct"] == "11"

    set_user_setting(app.csv_dir, "333", source.SOURCE_KEY, source.SOURCE_SELF, chain_id="*")
    assert sibot.user_settings(app, "333", 0)["allocation_pct"] == "33"


def test_chain_specific_main_master_override_is_inherited(tmp_path):
    app = _app(tmp_path)
    _write_users(app)
    set_user_setting(app.csv_dir, "111", "sibot_allocation_pct", "12", chain_id="*")
    set_user_setting(app.csv_dir, "111", "sibot_allocation_pct", "9", chain_id=137)
    set_user_setting(app.csv_dir, "333", source.SOURCE_KEY, source.SOURCE_PRIMARY_MASTER, chain_id="*")

    assert sibot.user_settings(app, "333", 1)["allocation_pct"] == "12"
    assert sibot.user_settings(app, "333", 137)["allocation_pct"] == "9"


def test_settings_keyboard_exposes_named_sibot_source_options(tmp_path):
    app = _app(tmp_path)
    _write_users(app)
    kb = source.settings_keyboard(app, "333")
    first = kb["inline_keyboard"][0]
    assert {b["callback_data"] for b in first} == {"sibot:source:self", "sibot:source:primary"}
    assert any("My settings" in b["text"] for b in first)
    assert any("Main Master" in b["text"] for b in first)
