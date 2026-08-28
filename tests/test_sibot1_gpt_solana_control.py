from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from learnerbot import sibot1_gpt_solana_control_patch as gptctl


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    csv_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir, telegram_bot_token="")


def test_gpt_arm_is_engine_specific_and_does_not_require_rpc(tmp_path, monkeypatch):
    app = _app(tmp_path)
    notifications = []

    monkeypatch.setattr(gptctl, "is_master", lambda csv_dir, tid: True)
    monkeypatch.setattr(gptctl, "_signer_vault_status", lambda app, tid: (True, "ready", "WalletAddress"))
    monkeypatch.setattr(gptctl._bridge, "_account_gate", lambda app, tid: (True, "ok"))
    monkeypatch.setattr(gptctl, "_notify", lambda app, tid, text: notifications.append(str(text)))
    monkeypatch.setattr(gptctl, "_send_status", lambda app, tid: None)

    def rpc_must_not_run(*args, **kwargs):
        raise AssertionError("ARM must not call Solana RPC/getBalance")

    monkeypatch.setattr(gptctl, "_balance_status", rpc_must_not_run)

    assert gptctl._command(app, "123", "/gptsolarm on CONFIRM") is True
    row = gptctl.control(app, "123")
    assert row["armed"] == "true"
    assert row["live_enabled"] == "false"
    assert row["auto_enabled"] == "false"
    assert gptctl.configured(app, "123") is True
    assert any("GPT Solana ARMED" in text for text in notifications)


def test_gpt_specific_control_does_not_change_legacy_control_file(tmp_path, monkeypatch):
    app = _app(tmp_path)
    legacy_path = gptctl._bridge._control_path(app)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        "telegram_id,armed,live_enabled,auto_enabled,max_sol_per_trade,updated_epoch\n"
        "123,false,true,true,0.0005,1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(gptctl, "is_master", lambda csv_dir, tid: True)
    monkeypatch.setattr(gptctl, "_signer_vault_status", lambda app, tid: (True, "ready", "WalletAddress"))
    monkeypatch.setattr(gptctl._bridge, "_account_gate", lambda app, tid: (True, "ok"))
    monkeypatch.setattr(gptctl, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(gptctl, "_send_status", lambda *args, **kwargs: None)

    before = legacy_path.read_text(encoding="utf-8")
    gptctl._command(app, "123", "/gptsolarm on CONFIRM")
    after = legacy_path.read_text(encoding="utf-8")

    assert before == after
    assert gptctl.control(app, "123")["armed"] == "true"
    assert gptctl.control(app, "123")["live_enabled"] == "false"


def test_gpt_readiness_switches_only_after_gpt_control_is_configured(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(gptctl, "_PREV_READINESS", lambda app, tid: {"source": "legacy"})
    monkeypatch.setattr(gptctl, "readiness", lambda app, tid: {"source": "gpt"})

    gptctl._TLS.engine_id = "gpt"
    try:
        assert gptctl._readiness_engine_aware(app, "123")["source"] == "legacy"
        gptctl.set_control(app, "123", armed="true")
        assert gptctl._readiness_engine_aware(app, "123")["source"] == "gpt"
        gptctl._TLS.engine_id = "grok"
        assert gptctl._readiness_engine_aware(app, "123")["source"] == "legacy"
    finally:
        try:
            delattr(gptctl._TLS, "engine_id")
        except AttributeError:
            pass


def test_gpt_status_separates_signer_and_rpc_health(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(
        gptctl,
        "readiness",
        lambda app, tid: {
            "control": {"armed": "true", "live_enabled": "false", "auto_enabled": "false"},
            "signer_ready": True,
            "rpc_ready": False,
            "rpc_detail": "SolanaRpcEndpointError: Solana RPC getBalance: HTTP 401",
            "account_ready": True,
            "funded": False,
            "balance_sol": Decimal("0"),
            "entry_size_sol": Decimal("0.0005"),
            "reserve_sol": Decimal("0.005"),
        },
    )

    text = gptctl.status_text(app, "123")
    assert "GPT BOT" in text
    assert "GPT ARMED" in text
    assert "Signer vault" in text and "READY" in text
    assert "Solana RPC" in text and "NOT READY" in text
    assert "HTTP 401" in text
    assert "Gemini/Grok/Claude controls" in text
