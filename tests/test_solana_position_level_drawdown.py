import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_profit_guard_patch as guard
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


def test_position_scaled_out_with_a_losing_leg_first_is_not_a_fake_drawdown(tmp_path):
    # One buy lot, sold off in two pieces: first half at a loss, second half at a
    # large gain. Fragment-level equity dips hard after the first (losing) leg
    # before recovering, registering a large "drawdown" for what was actually a
    # single strongly profitable position with no real peak-to-trough decline.
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 100, cost=Decimal(5), proceeds=Decimal(2))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 200, cost=Decimal(5), proceeds=Decimal(20))
        conn.commit()

    m = guard.quality_metrics(app, "w1", {"lookback_days": "60", "recent_trade_window": "5"})

    assert m["fragment_drawdown_pct"] == Decimal(60)  # -60% dip after the first leg
    assert m["drawdown_pct"] == Decimal(0)  # the position itself only ever went up


def test_drawdown_gate_flips_on_position_level_scoring(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buyB", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 100, cost=Decimal(5), proceeds=Decimal(2))
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buyB", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 200, cost=Decimal(5), proceeds=Decimal(20))
        conn.commit()

    cfg = {"lookback_days": "60", "recent_trade_window": "20", "require_complete_history": "false",
           "min_closed_trades": "1", "min_win_rate_pct": "0", "min_recent_win_rate_pct": "0",
           "min_profit_factor": "0", "min_recent_profit_factor": "0", "max_leader_drawdown_pct": "30"}
    m = guard.quality_metrics(app, "w2", cfg)

    # The old fragment-level number would have failed a 30% drawdown cap outright.
    assert m["fragment_drawdown_pct"] > Decimal(30)
    assert guard._historical_ok(m, cfg)  # position-level drawdown (0%) clears the same cap
