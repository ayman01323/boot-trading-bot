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


def test_scaled_out_winning_position_is_not_fragmented_into_losses(tmp_path):
    # One buy, sold off in three tranches as price rises: -1 SOL, -0.5 SOL, +3 SOL
    # legs. Fragment-level: 1 win / 3 = 33% (fails a 65% floor). The position as a
    # whole nets +1.5 SOL -- a single win. This is exactly the "scale out in stages"
    # pattern real traders use, which the old per-fragment win_rate punished.
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 100, cost=Decimal(3), proceeds=Decimal(2))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 200, cost=Decimal(3), proceeds=Decimal("2.5"))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s3",
                      buy_ts=buy_ts, sell_ts=buy_ts + 300, cost=Decimal(3), proceeds=Decimal(6))
        conn.commit()

    m = guard.quality_metrics(app, "w1", {"lookback_days": "60", "recent_trade_window": "5"})

    assert m["fragment_win_rate"] == Decimal("100") / Decimal(3)  # 1 win of 3 fragments
    assert m["position_closed"] == 1
    assert m["win_rate"] == Decimal(100)  # the one real position was a net win


def test_re_entering_a_mint_after_fully_exiting_is_a_new_position(tmp_path):
    # Two independent round trips in the same mint: first one loses, fully exits,
    # then the wallet buys back in later and wins. These must NOT be merged into
    # one position just because they share a mint.
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy1", sell_sig="sell1",
                      buy_ts=NOW - 7200, sell_ts=NOW - 7150, cost=Decimal(2), proceeds=Decimal(1))
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy2", sell_sig="sell2",
                      buy_ts=NOW - 3600, sell_ts=NOW - 3550, cost=Decimal(2), proceeds=Decimal(4))
        conn.commit()

    m = guard.quality_metrics(app, "w2", {"lookback_days": "60", "recent_trade_window": "5"})

    assert m["position_closed"] == 2
    assert m["win_rate"] == Decimal(50)  # one loss, one win -> 1/2 positions won


def test_position_level_win_rate_can_flip_historical_ok_decision(tmp_path):
    # Reproduces the production pattern found via the live-DB audit: many small
    # fragments of one profitable position fail a 65% fragment-level floor, but
    # correctly pass once scored per closed position.
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        # 5 losing fragments (small) + 5 winning fragments (small), all one
        # continuous position that nets positive overall.
        ts = buy_ts
        for i in range(5):
            _insert_trade(conn, wallet="w3", mint="MINT_C", buy_sig="buyC", sell_sig=f"loss{i}",
                          buy_ts=buy_ts, sell_ts=ts, cost=Decimal(1), proceeds=Decimal("0.8"))
            ts += 10
        for i in range(5):
            _insert_trade(conn, wallet="w3", mint="MINT_C", buy_sig="buyC", sell_sig=f"win{i}",
                          buy_ts=buy_ts, sell_ts=ts, cost=Decimal(1), proceeds=Decimal(3))
            ts += 10
        conn.commit()

    cfg = {"lookback_days": "60", "recent_trade_window": "20", "min_win_rate_pct": "65",
           "min_recent_win_rate_pct": "65", "require_complete_history": "false",
           "min_closed_trades": "1", "min_profit_factor": "0", "max_leader_drawdown_pct": "100",
           "min_recent_profit_factor": "0"}
    m = guard.quality_metrics(app, "w3", cfg)

    assert m["fragment_win_rate"] == Decimal(50)  # 5 of 10 fragments won -> fails 65%
    assert not guard._historical_ok(dict(m, win_rate=m["fragment_win_rate"],
                                          recent_win_rate=m["fragment_win_rate"]), cfg)

    assert m["position_closed"] == 1
    assert m["win_rate"] == Decimal(100)  # the single real position was profitable
    assert guard._historical_ok(m, cfg)
