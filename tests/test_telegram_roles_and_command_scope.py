from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace


def _write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def test_role_patch_never_mirrors_or_changes_trading_settings(tmp_path):
    from learnerbot import telegram_account_roles_patch as roles
    from learnerbot.user_registry import USER_HEADERS, get_user

    csv_dir = tmp_path / "CSVbot"
    data_dir = tmp_path / "data"
    rows = [
        {"telegram_id": "5923828381", "role": "USER", "status": "ACTIVE", "fee_plan_id": "MASTER", "label": "old", "allowed_chains": "*", "max_wallets": "20", "can_transfer": "true", "can_manual_trade": "true", "can_auto_trade": "true", "created_epoch": "1", "activated_epoch": "1", "notes": ""},
        {"telegram_id": "6760898817", "role": "USER", "status": "PENDING", "fee_plan_id": "MASTER", "label": "old", "allowed_chains": "*", "max_wallets": "20", "can_transfer": "true", "can_manual_trade": "true", "can_auto_trade": "true", "created_epoch": "1", "activated_epoch": "", "notes": ""},
        {"telegram_id": "461513364", "role": "MASTER", "status": "ACTIVE", "fee_plan_id": "STANDARD", "label": "old", "allowed_chains": "*", "max_wallets": "5", "can_transfer": "true", "can_manual_trade": "true", "can_auto_trade": "true", "created_epoch": "1", "activated_epoch": "1", "notes": ""},
    ]
    _write_csv(csv_dir / "users.csv", USER_HEADERS, rows)
    settings = csv_dir / "user_trading_settings.csv"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        "telegram_id,chain_id,setting,value,description\n"
        "5923828381,0,sibot_allocation_pct,17,master value\n"
        "461513364,0,sibot_allocation_pct,9,user value\n",
        encoding="utf-8",
    )
    before = settings.read_bytes()
    app = SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)

    roles._ensure_roles(app)

    assert get_user(csv_dir, "5923828381")["role"] == "MASTER"
    assert get_user(csv_dir, "6760898817")["role"] == "MASTER"
    assert get_user(csv_dir, "6760898817")["status"] == "PENDING"  # preserve status
    assert get_user(csv_dir, "461513364")["role"] == "USER"
    assert get_user(csv_dir, "461513364")["status"] == "ACTIVE"
    assert get_user(csv_dir, "5882384847")["role"] == "USER"
    assert get_user(csv_dir, "5882384847")["status"] == "ACTIVE"
    assert settings.read_bytes() == before


def test_blue_command_menu_is_role_and_status_scoped(monkeypatch, tmp_path):
    from learnerbot import telegram_command_scope_patch as scope

    calls = []
    commands = [
        {"command": "menu", "description": "Open main menu"},
        {"command": "join", "description": "Register Telegram ID under default fee plan"},
        {"command": "activate", "description": "Activate account with code"},
        {"command": "fees", "description": "Show my fee plan/status"},
        {"command": "mode", "description": "Set mode"},
        {"command": "wallet", "description": "My wallet"},
        {"command": "sibot", "description": "Open SiBot dashboard"},
        {"command": "control", "description": "MASTER platform controls"},
        {"command": "engine", "description": "Pause/resume engine"},
    ]

    monkeypatch.setattr(scope, "_PREV_SET_COMMANDS", lambda token: None)
    monkeypatch.setattr(scope, "_csv_dir", lambda: tmp_path)
    monkeypatch.setattr(scope, "all_users", lambda path: [
        {"telegram_id": "5923828381", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "6760898817", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "5882384847", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "461513364", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "777000111", "role": "USER", "status": "PENDING"},
    ])

    def fake_json(method, token, *, payload=None, params=None, timeout=20):
        if method == "getMyCommands":
            return commands
        if method == "setMyCommands":
            calls.append(payload)
            return True
        raise AssertionError(method)

    monkeypatch.setattr(scope._tg, "_json", fake_json)
    scope.set_commands("TOKEN")

    chat_scopes = {
        str(x["scope"].get("chat_id")): {c["command"] for c in x["commands"]}
        for x in calls
        if x.get("scope", {}).get("type") == "chat"
    }
    assert "control" in chat_scopes["5923828381"]
    assert "engine" in chat_scopes["6760898817"]

    # ACTIVE normal users see only /menu in Telegram's blue command sheet.
    assert chat_scopes["5882384847"] == {"menu"}
    assert chat_scopes["461513364"] == {"menu"}

    # Pending users retain only the commands needed to get activated/check status.
    assert chat_scopes["777000111"] == {"menu", "join", "activate", "fees"}

    default_scopes = [
        x for x in calls
        if x.get("scope", {}).get("type") in {"default", "all_private_chats"}
    ]
    assert default_scopes
    for payload in default_scopes:
        assert {c["command"] for c in payload["commands"]} == {"menu", "join", "activate"}
