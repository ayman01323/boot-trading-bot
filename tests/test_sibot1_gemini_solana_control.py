from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from learnerbot import sibot1_gemini_solana_control_patch as gemctl


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    csv_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir, telegram_bot_token="")


def test_gemini_arm_live_sets_only_gemini_control(tmp_path, monkeypatch):
    app = _app(tmp_path)
    notifications = []
    legacy = gemctl._bridge._control_path(app)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "telegram_id,armed,live_enabled,auto_enabled,max_sol_per_trade,updated_epoch\n"
        "123,false,false,false,0.009,1\n",
        encoding="utf-8",
    )
    before = legacy.read_text(encoding="utf-8")

    monkeypatch.setattr(gemctl, "is_master", lambda csv_dir, tid: True)
    monkeypatch.setattr(gemctl, "_ready_for_live", lambda app, tid: (True, {}))
    monkeypatch.setattr(gemctl, "_notify", lambda app, tid, text: notifications.append(str(text)))
    monkeypatch.setattr(gemctl, "_send_status", lambda app, tid: None)

    assert gemctl._command(app, "123", "/gemini_arm_live CONFIRM") is True
    row = gemctl.control(app, "123")
    assert row["armed"] == "true"
    assert row["live_enabled"] == "true"
    assert row["auto_enabled"] == "false"
    assert legacy.read_text(encoding="utf-8") == before
    assert any("GEMINI ARMED + LIVE" in text for text in notifications)


def test_gemini_auto_requires_explicit_confirmation(tmp_path, monkeypatch):
    app = _app(tmp_path)
    notifications = []
    gemctl.set_control(app, "123", armed="true", live_enabled="true", auto_enabled="false")

    monkeypatch.setattr(gemctl, "is_master", lambda csv_dir, tid: True)
    monkeypatch.setattr(gemctl, "_ready_for_live", lambda app, tid: (True, {}))
    monkeypatch.setattr(gemctl, "_notify", lambda app, tid, text: notifications.append(str(text)))
    monkeypatch.setattr(gemctl, "_send_status", lambda app, tid: None)

    gemctl._command(app, "123", "/gemini_auto on")
    assert gemctl.control(app, "123")["auto_enabled"] == "false"

    gemctl._command(app, "123", "/gemini_auto on CONFIRM")
    assert gemctl.control(app, "123")["auto_enabled"] == "true"


def test_gemini_disarm_clears_all_entry_authority(tmp_path, monkeypatch):
    app = _app(tmp_path)
    gemctl.set_control(app, "123", armed="true", live_enabled="true", auto_enabled="true")

    monkeypatch.setattr(gemctl, "is_master", lambda csv_dir, tid: True)
    monkeypatch.setattr(gemctl, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(gemctl, "_send_status", lambda *args, **kwargs: None)

    gemctl._command(app, "123", "/gemini_disarm")
    row = gemctl.control(app, "123")
    assert row["armed"] == "false"
    assert row["live_enabled"] == "false"
    assert row["auto_enabled"] == "false"


def test_gemini_status_labels_dedicated_engine(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(
        gemctl,
        "readiness",
        lambda app, tid: {
            "control": {"armed": "false", "live_enabled": "false", "auto_enabled": "false"},
            "signer_ready": True,
            "rpc_ready": True,
            "rpc_detail": "ready",
            "account_ready": True,
            "funded": True,
            "balance_sol": Decimal("0.050000000"),
            "entry_size_sol": Decimal("0.009"),
            "reserve_sol": Decimal("0.005"),
        },
    )
    text = gemctl.status_text(app, "123")
    assert "GEMINI TRADING BOT" in text
    assert "Gemini candidates only" in text
    assert "/gemini_arm_live CONFIRM" in text
    assert "PoolCheck" in text
