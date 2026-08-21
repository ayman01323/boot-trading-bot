from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import learnerbot.telegram_master_pnl_report_patch as report


def test_period_presets_and_calendar_month_math():
    now = int(datetime(2026, 3, 31, 12, 30, tzinfo=timezone.utc).timestamp())
    seven = report.parse_period("7d", now_ts=now)
    assert seven["cutoff"] == now - 7 * 86400
    assert seven["label"] == "Last 7 days"

    one_month = report.parse_period("1m", now_ts=now)
    cutoff = datetime.fromtimestamp(one_month["cutoff"], tz=timezone.utc)
    assert cutoff == datetime(2026, 2, 28, 12, 30, tzinfo=timezone.utc)
    assert one_month["label"] == "Last 1 calendar month"


def test_period_rejects_bad_or_unbounded_values():
    for value in ("0d", "0m", "abc", "7", "3651d", "121m"):
        try:
            report.parse_period(value, now_ts=1_800_000_000)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {value}")


def test_route_rows_use_realised_net_after_recorded_profit_fee(tmp_path):
    csv_dir = tmp_path / "csv"
    auto = csv_dir / "auto"
    auto.mkdir(parents=True)
    (auto / "auto_trade_execution.csv").write_text(
        "timestamp_epoch,telegram_id,chain_id,chain_slug,realised_net_base,profit_fee_base,status\n"
        "2000,u1,1,ethereum,1.25,0.25,SUCCESS\n"
        "2001,u2,1,ethereum,-0.40,0,FAILED\n"
        "1000,u3,1,ethereum,5,1,SUCCESS\n",
        encoding="utf-8",
    )
    app = SimpleNamespace(csv_dir=csv_dir)
    rows = report._route_rows(app, 1500)
    assert len(rows) == 1
    assert rows[0]["telegram_id"] == "u1"
    assert rows[0]["pnl"] == Decimal("1.00")
    assert rows[0]["fee"] == Decimal("0.25")


def test_collect_all_users_pnl_aggregates_each_chain_and_source(monkeypatch):
    app = SimpleNamespace(csv_dir="unused", data_dir="unused")
    monkeypatch.setattr(report, "_chain_catalogue", lambda app: {
        1: {"chain_id": 1, "slug": "ethereum", "name": "Ethereum", "symbol": "ETH"},
        -101: {"chain_id": -101, "slug": "solana", "name": "Solana", "symbol": "SOL"},
    })
    monkeypatch.setattr(report, "_route_rows", lambda app, cutoff: [
        {"telegram_id": "u1", "chain_id": 1, "chain_slug": "ethereum", "pnl": Decimal("1.0"), "fee": Decimal("0.1"), "source": "EVM Routes"},
        {"telegram_id": "u2", "chain_id": 1, "chain_slug": "ethereum", "pnl": Decimal("-0.4"), "fee": Decimal("0"), "source": "EVM Routes"},
    ])
    monkeypatch.setattr(report, "_evm_sibot_rows", lambda app, cutoff: [
        {"telegram_id": "u1", "chain_id": 1, "chain_slug": "ethereum", "pnl": Decimal("0.5"), "fee": Decimal("0.05"), "source": "EVM SiBot"},
        {"telegram_id": "u3", "chain_id": 1, "chain_slug": "ethereum", "pnl": Decimal("0"), "fee": Decimal("0"), "source": "EVM SiBot"},
    ])
    monkeypatch.setattr(report, "_solana_rows", lambda app, cutoff: [
        {"telegram_id": "u2", "chain_id": -101, "chain_slug": "solana", "pnl": Decimal("2"), "fee": Decimal("0"), "source": "Solana SiBot"},
        {"telegram_id": "u4", "chain_id": -101, "chain_slug": "solana", "pnl": Decimal("-1"), "fee": Decimal("0"), "source": "Solana SiBot"},
    ])

    result = report.collect_all_users_pnl(app, "7d", now_ts=1_800_000_000)
    assert result["trade_count"] == 6
    assert result["user_count"] == 4
    by_id = {c["chain_id"]: c for c in result["chains"]}

    eth = by_id[1]
    assert eth["wins"] == 2
    assert eth["losses"] == 1
    assert eth["breakeven"] == 1
    assert eth["winning_pnl"] == Decimal("1.5")
    assert eth["losing_pnl"] == Decimal("0.4")
    assert eth["net_pnl"] == Decimal("1.1")
    assert eth["profit_fees"] == Decimal("0.15")
    assert eth["user_count"] == 3
    assert eth["sources"] == {"EVM Routes": 2, "EVM SiBot": 2}

    sol = by_id[-101]
    assert sol["wins"] == 1
    assert sol["losses"] == 1
    assert sol["net_pnl"] == Decimal("1")
    assert sol["user_count"] == 2


def test_period_keyboard_has_all_requested_options():
    data = [
        button["callback_data"]
        for row in report.period_keyboard()["inline_keyboard"]
        for button in row
    ]
    for expected in (
        "allpnl:p:7d", "allpnl:p:14d", "allpnl:p:30d",
        "allpnl:p:2m", "allpnl:p:3m", "allpnl:p:6m", "allpnl:p:12m",
        "allpnl:custom",
    ):
        assert expected in data
    custom = {
        button["callback_data"]
        for row in report.custom_keyboard()["inline_keyboard"]
        for button in row
    }
    assert "allpnl:custom:days" in custom
    assert "allpnl:custom:months" in custom


def test_all_users_report_button_is_master_only(monkeypatch):
    monkeypatch.setattr(report, "_PREV_MENU", lambda app, chat_id: {
        "inline_keyboard": [[{"text": "Home", "callback_data": "menu:home"}]]
    })
    app = SimpleNamespace(csv_dir="csv")

    monkeypatch.setattr(report, "is_master", lambda csv_dir, tid: tid == "master")
    master_buttons = [
        b["callback_data"]
        for row in report.menu_keyboard(app, "master")["inline_keyboard"]
        for b in row
    ]
    user_buttons = [
        b["callback_data"]
        for row in report.menu_keyboard(app, "user")["inline_keyboard"]
        for b in row
    ]
    assert "menu:allpnl" in master_buttons
    assert "menu:allpnl" not in user_buttons
