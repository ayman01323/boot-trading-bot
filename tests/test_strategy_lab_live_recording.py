import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from learnerbot import strategy_lab as lab
from learnerbot import strategy_lab_live_recording_patch as recording
from learnerbot import sibot_strategy_lab_throttle_patch as throttle


def _app(tmp_path: Path):
    data_dir = tmp_path / "data"
    csv_dir = tmp_path / "CSVbot"
    data_dir.mkdir()
    csv_dir.mkdir()
    return SimpleNamespace(data_dir=data_dir, csv_dir=csv_dir)


def test_accumulate_and_record_sums_across_calls(tmp_path):
    app = _app(tmp_path)
    sid = recording.leader_copy_strategy_id(app, "test_chain_accumulate")
    now = int(time.time())
    ws, we = recording._hour_bounds(now)

    recording._accumulate_and_record(
        app, sid, "LIVE", ws, we,
        trades=1, wins=1, losses=0, gross_profit=Decimal("2"), gross_loss=Decimal("0"),
    )
    recording._accumulate_and_record(
        app, sid, "LIVE", ws, we,
        trades=1, wins=0, losses=1, gross_profit=Decimal("0"), gross_loss=Decimal("1"),
    )

    m = lab.strategy_metrics(app, sid, mode="LIVE")
    assert m["trades"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 1
    assert m["net_profit"] == "1"


def test_close_position_records_win_and_loss(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = []

    def fake_close(app_, position_id, fraction=Decimal(1), reason="EXIT"):
        calls.append(position_id)
        return {"position_id": position_id, "closed": True, "realised_net_native": Decimal("0"), "user_net_native": Decimal("0"), "tx_hash": ""}

    monkeypatch.setattr(recording, "_PREV_CLOSE_POSITION", fake_close)

    from learnerbot import sibot as sibot_mod

    def fake_connect(app_):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE positions(position_id TEXT, chain_slug TEXT, mode TEXT, realised_net_native TEXT)")
        conn.execute(
            "INSERT INTO positions VALUES(?,?,?,?)",
            ("pos1", "test_chain_close", "LIVE", "3.5"),
        )
        conn.commit()
        return conn

    monkeypatch.setattr(sibot_mod, "connect", fake_connect)

    result = recording.close_position_with_strategy_lab(app, "pos1")
    assert result["closed"] is True
    assert calls == ["pos1"]

    sid = recording.leader_copy_strategy_id(app, "test_chain_close")
    m = lab.strategy_metrics(app, sid, mode="LIVE")
    assert m["trades"] == 1
    assert m["wins"] == 1
    assert m["net_profit"] == "3.5"


def test_throttle_reduces_size_when_strategy_replaced(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain_slug = "test_chain_throttle"
    sid = recording.leader_copy_strategy_id(app, chain_slug)

    now = int(time.time())
    for i in range(3):
        ws, we = recording._hour_bounds(now - (3 - i) * 3600)
        recording._accumulate_and_record(
            app, sid, "LIVE", ws, we,
            trades=3, wins=0, losses=3, gross_profit=Decimal("0"), gross_loss=Decimal("10"),
        )

    throttle._cache.clear()
    multiplier, status = throttle._strategy_lab_multiplier(app, chain_slug)
    assert status == "REPLACE"
    assert multiplier == Decimal("0.25")

    trader = SimpleNamespace(chain=SimpleNamespace(slug=chain_slug))

    def fake_prev(app_, tid, trader_, cfg):
        return Decimal("1.0")

    monkeypatch.setattr(throttle, "_PREV_POSITION_SIZE", fake_prev)
    throttle._cache.clear()
    amount = throttle._position_size_with_strategy_lab(app, "tid", trader, {"min_trade_native": "0.0001"})
    assert amount == Decimal("0.25")


def test_throttle_is_noop_with_insufficient_evidence(tmp_path, monkeypatch):
    app = _app(tmp_path)
    chain_slug = "test_chain_no_evidence"
    trader = SimpleNamespace(chain=SimpleNamespace(slug=chain_slug))

    def fake_prev(app_, tid, trader_, cfg):
        return Decimal("1.0")

    monkeypatch.setattr(throttle, "_PREV_POSITION_SIZE", fake_prev)
    throttle._cache.clear()
    amount = throttle._position_size_with_strategy_lab(app, "tid", trader, {})
    assert amount == Decimal("1.0")


def test_market_native_progress_records_from_csv(tmp_path):
    app = _app(tmp_path)
    auto_dir = app.csv_dir / "auto"
    auto_dir.mkdir(parents=True)
    path = auto_dir / "auto_trade_execution.csv"
    now = int(time.time())
    headers = ["timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug", "route_id",
               "route_path", "input_base", "expected_gross_base", "expected_gas_base", "expected_net_base",
               "realised_net_base", "profit_fee_base", "fee_tx_hash", "tx_hash", "status", "note"]
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerow({**{h: "" for h in headers}, "timestamp_epoch": str(now), "chain_slug": "test_chain_market",
                    "realised_net_base": "0.02", "status": "SUCCESS"})
        w.writerow({**{h: "" for h in headers}, "timestamp_epoch": str(now), "chain_slug": "test_chain_market",
                    "realised_net_base": "-0.01", "status": "SUCCESS"})
        w.writerow({**{h: "" for h in headers}, "timestamp_epoch": str(now), "chain_slug": "test_chain_market",
                    "realised_net_base": "", "status": "REJECTED"})

    recording._record_market_native_progress(app)

    sid = recording._market_native_strategy_id(app, "test_chain_market")
    m = lab.strategy_metrics(app, sid, mode="LIVE")
    assert m["trades"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 1

    # Re-running with no new rows must not double-count (cursor advances).
    recording._record_market_native_progress(app)
    m2 = lab.strategy_metrics(app, sid, mode="LIVE")
    assert m2["trades"] == 2
