import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_positive_edge_entry_gate_patch as gate
from learnerbot import solana_sibot as sol

NOW = int(time.time())


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _insert_trade(conn, *, wallet, mint, buy_sig, sell_sig, buy_ts, sell_ts, cost, proceeds):
    net = proceeds - cost
    trade_id = f"{wallet}|{mint}|{buy_sig}|{sell_sig}"
    conn.execute(
        """INSERT INTO trades(trade_id,wallet,mint,decimals,buy_signature,sell_signature,buy_ts,sell_ts,
                               token_amount_raw,cost_sol,proceeds_sol,net_sol,hold_seconds,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (trade_id, wallet, mint, 6, buy_sig, sell_sig, buy_ts, sell_ts,
         "1000", str(cost), str(proceeds), str(net), max(0, sell_ts - buy_ts), "TEST", sell_ts),
    )


def test_median_return_scored_per_position_not_per_fragment(tmp_path):
    # A single position scaled out in two legs: -20% then +140%. Fragment-level
    # median of two returns is their average-of-middle-two, (-20+140)/2 = 60% --
    # which happens to look fine here, so use three legs to show fragment-level
    # can differ sharply from the position's real aggregate return.
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 10, cost=Decimal(1), proceeds=Decimal("0.5"))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 20, cost=Decimal(1), proceeds=Decimal("0.5"))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s3",
                      buy_ts=buy_ts, sell_ts=buy_ts + 30, cost=Decimal(1), proceeds=Decimal(6))
        conn.commit()

    m = gate.leader_return_edge(app, "w1", {"lookback_days": "60", "live_edge_recent_trade_window": "10"})

    # Fragment view: -50%, -50%, +500% -> median of the three is -50%.
    assert m["fragment_median_return_pct"] == Decimal(-50)
    # Position view: one position, cost 3 / net 4 (proceeds 7 - cost 3) -> +133.33%.
    assert m["position_closed"] == 1
    assert m["median_return_pct"] == (Decimal(400) / Decimal(3))


def test_edge_gate_flips_on_position_level_median_return(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buyB", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 10, cost=Decimal(1), proceeds=Decimal("0.5"))
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buyB", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 20, cost=Decimal(1), proceeds=Decimal("0.5"))
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buyB", sell_sig="s3",
                      buy_ts=buy_ts, sell_ts=buy_ts + 30, cost=Decimal(1), proceeds=Decimal(6))
        conn.commit()

    cfg = {"lookback_days": "60", "live_edge_recent_trade_window": "10",
           "min_closed_trades": "1", "live_min_leader_median_return_pct": "5",
           "live_min_leader_recent_median_return_pct": "4"}
    m = gate.leader_return_edge(app, "w2", cfg)

    # The old fragment-level number would have failed the 5% floor outright,
    # even though the position itself was a strong win.
    assert m["fragment_median_return_pct"] < Decimal("5")

    ok, reason, metrics = gate._edge_ok(app, "w2", cfg)
    assert ok is True, reason
    assert metrics["median_return_pct"] >= Decimal("5")
