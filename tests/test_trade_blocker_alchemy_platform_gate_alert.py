from types import SimpleNamespace

from learnerbot import telegram_trade_blocker_health_patch as health
from learnerbot import trade_blocker_alchemy_history_patch as alchemy_patch


def _sample_snapshot(*, platform_auto, platform_live):
    return {
        "generated_epoch": 1,
        "polygon_focus": True,
        "evm_history_providers": {"polygon": "ALCHEMY"},
        "platform_auto": platform_auto,
        "platform_live": platform_live,
        "evm": {},
        "fast_market": {},
    }


def _master_app(tmp_path):
    app = SimpleNamespace(
        csv_dir=tmp_path / "csv",
        data_dir=tmp_path / "data",
        etherscan_api_key="",
        telegram_bot_token="test-token",
    )
    app.csv_dir.mkdir()
    return app


def test_alchemy_startup_health_alerts_master_when_platform_gate_off(tmp_path, monkeypatch):
    app = _master_app(tmp_path)
    monkeypatch.setattr(
        health, "all_users",
        lambda *a, **k: [{"telegram_id": "555", "role": "MASTER"}],
    )
    monkeypatch.setattr(
        alchemy_patch, "_snapshot",
        lambda app, tid: _sample_snapshot(platform_auto=False, platform_live=True),
    )
    sent = []
    monkeypatch.setattr(health, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))

    alchemy_patch._publish_startup_health(app)

    gate_alerts = [text for _, text in sent if "Platform trading gate is OFF" in text]
    assert len(gate_alerts) == 1
    assert "AUTO (execution)" in gate_alerts[0]
    assert "LIVE (signing)" not in gate_alerts[0]
    assert (app.data_dir / ".platform_gate_off_warning_epoch").exists()


def test_alchemy_startup_health_platform_gate_alert_is_throttled(tmp_path, monkeypatch):
    app = _master_app(tmp_path)
    monkeypatch.setattr(
        health, "all_users",
        lambda *a, **k: [{"telegram_id": "555", "role": "MASTER"}],
    )
    monkeypatch.setattr(
        alchemy_patch, "_snapshot",
        lambda app, tid: _sample_snapshot(platform_auto=False, platform_live=False),
    )
    sent = []
    monkeypatch.setattr(health, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))

    alchemy_patch._publish_startup_health(app)
    alchemy_patch._publish_startup_health(app)

    gate_alerts = [text for _, text in sent if "Platform trading gate is OFF" in text]
    assert len(gate_alerts) == 1
    assert "LIVE (signing)" in gate_alerts[0] and "AUTO (execution)" in gate_alerts[0]


def test_alchemy_startup_health_no_gate_alert_when_platform_gates_on(tmp_path, monkeypatch):
    app = _master_app(tmp_path)
    monkeypatch.setattr(
        health, "all_users",
        lambda *a, **k: [{"telegram_id": "555", "role": "MASTER"}],
    )
    monkeypatch.setattr(
        alchemy_patch, "_snapshot",
        lambda app, tid: _sample_snapshot(platform_auto=True, platform_live=True),
    )
    sent = []
    monkeypatch.setattr(health, "send_message", lambda token, tid, text, **kw: sent.append((tid, text)))

    alchemy_patch._publish_startup_health(app)

    gate_alerts = [text for _, text in sent if "Platform trading gate is OFF" in text]
    assert gate_alerts == []
    assert not (app.data_dir / ".platform_gate_off_warning_epoch").exists()
