from types import SimpleNamespace

import pytest

from learnerbot import hourly_capital_alert_patch as hourly
from learnerbot import telegram_user_menu_compact_patch as menu


def test_report_interval_parser_accepts_minutes_and_hours():
    assert menu._parse_interval("30m") == 30
    assert menu._parse_interval("1h") == 60
    assert menu._parse_interval("1.5h") == 90
    assert menu._parse_interval("120") == 120


@pytest.mark.parametrize("raw", ["4m", "25h", "banana"])
def test_report_interval_parser_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        menu._parse_interval(raw)


def test_loss_threshold_parser_is_user_configurable():
    assert menu._parse_threshold("10") == "10"
    assert menu._parse_threshold("12.5%") == "12.5"
    with pytest.raises(ValueError):
        menu._parse_threshold("0.5")
    with pytest.raises(ValueError):
        menu._parse_threshold("96")


def test_compact_user_menu_contains_reports_and_alerts(monkeypatch):
    monkeypatch.setattr(menu, "is_master", lambda csv_dir, tid: False)
    app = SimpleNamespace(csv_dir="/tmp")
    kb = menu.menu_keyboard(app, 123)
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "menu:myalerts" in callbacks


def test_periodic_report_is_opt_in_by_default(monkeypatch):
    monkeypatch.setattr(hourly, "user_setting", lambda csv_dir, tid, chain_id, key, default=None: default)
    app = SimpleNamespace(csv_dir="/tmp")
    assert hourly.report_enabled(app, "123") is False
    assert hourly.report_interval_minutes(app, "123") == 60
    assert hourly.loss_alert_enabled(app, "123") is False
    assert hourly.loss_alert_threshold_pct(app, "123") == 10


def test_live_loss_rows_ignore_shadow_and_respect_threshold(monkeypatch):
    chain = SimpleNamespace(chain_id=8453, name="Base")
    monkeypatch.setattr(hourly, "load_chains", lambda app, enabled_only=False: [chain])
    monkeypatch.setattr(
        hourly._sibot,
        "position_rows",
        lambda app, tid, open_only=True: [
            {"position_id": "evm-live", "chain_id": 8453, "mode": "LIVE", "symbol": "AAA", "unrealised_pct": -11, "leader_exit_pending": 0},
            {"position_id": "evm-shadow", "chain_id": 8453, "mode": "SHADOW", "symbol": "BBB", "unrealised_pct": -50, "leader_exit_pending": 0},
            {"position_id": "evm-ok", "chain_id": 8453, "mode": "LIVE", "symbol": "CCC", "unrealised_pct": -9, "leader_exit_pending": 0},
        ],
    )
    monkeypatch.setattr(
        hourly._sol,
        "position_rows",
        lambda app, tid, open_only=True: [
            {"position_id": "sol-live", "mode": "LIVE", "mint": "So11111111111111111111111111111111111111112", "unrealised_pct": -14, "leader_exit_pending": 1}
        ],
    )
    rows = hourly._live_loss_rows(object(), "123", hourly.Decimal("10"))
    assert {row["key"][2] for row in rows} == {"evm-live", "sol-live"}


def test_loss_alert_sends_once_per_threshold_crossing(monkeypatch):
    sent = []
    app = SimpleNamespace(telegram_bot_token="token", csv_dir="/tmp")
    monkeypatch.setattr(hourly, "loss_alert_enabled", lambda app, tid: True)
    monkeypatch.setattr(hourly, "loss_alert_threshold_pct", lambda app, tid: hourly.Decimal("10"))
    monkeypatch.setattr(
        hourly,
        "_live_loss_rows",
        lambda app, tid, threshold: [
            {"key": (str(tid), "solana", "p1"), "chain": "Solana", "asset": "TOKEN", "pct": hourly.Decimal("-12.5"), "pending": False}
        ],
    )
    monkeypatch.setattr(hourly, "send_message", lambda token, tid, text, parse_mode=None: sent.append((tid, text)))
    hourly._LOSS_ACTIVE = set()

    assert hourly.send_new_loss_alerts(app, "123") == 1
    assert hourly.send_new_loss_alerts(app, "123") == 0
    assert len(sent) == 1
    assert "-12.50%" in sent[0][1]
