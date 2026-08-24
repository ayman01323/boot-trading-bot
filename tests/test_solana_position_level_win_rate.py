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

    assert m["fragment_win_rate"] == Decimal("100") / Decimal(3)
    assert m["fragment_closed"] == 3
    assert m["position_closed"] == 1
    assert m["closed"] == 1
    assert m["win_rate"] == Decimal(100)


def test_re_entering_a_mint_after_fully_exiting_is_a_new_position(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy1", sell_sig="sell1",
                      buy_ts=NOW - 7200, sell_ts=NOW - 7150, cost=Decimal(2), proceeds=Decimal(1))
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy2", sell_sig="sell2",
                      buy_ts=NOW - 3600, sell_ts=NOW - 3550, cost=Decimal(2), proceeds=Decimal(4))
        conn.commit()

    m = guard.quality_metrics(app, "w2", {"lookback_days": "60", "recent_trade_window": "5"})

    assert m["position_closed"] == 2
    assert m["closed"] == 2
    assert m["win_rate"] == Decimal(50)


def test_position_level_win_rate_can_flip_historical_ok_decision(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
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

    assert m["fragment_win_rate"] == Decimal(50)
    assert not guard._historical_ok(dict(m, win_rate=m["fragment_win_rate"],
                                          recent_win_rate=m["fragment_win_rate"]), cfg)

    assert m["position_closed"] == 1
    assert m["win_rate"] == Decimal(100)
    assert guard._historical_ok(m, cfg)


def test_fifo_fragments_cannot_fake_minimum_closed_position_sample(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        for i in range(10):
            _insert_trade(conn, wallet="w4", mint="MINT_D", buy_sig="buyD", sell_sig=f"s{i}",
                          buy_ts=buy_ts, sell_ts=buy_ts + i, cost=Decimal(1), proceeds=Decimal(2))
        conn.commit()

    cfg = {"lookback_days": "60", "recent_trade_window": "20", "min_win_rate_pct": "0",
           "min_recent_win_rate_pct": "0", "require_complete_history": "false",
           "min_closed_trades": "10", "min_profit_factor": "0", "max_leader_drawdown_pct": "100",
           "min_recent_profit_factor": "0"}
    m = guard.quality_metrics(app, "w4", cfg)

    assert m["fragment_closed"] == 10
    assert m["position_closed"] == 1
    assert m["closed"] == 1
    assert guard._historical_ok(m, cfg) is False


def test_recent_window_uses_complete_positions_not_last_fifo_fragments(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        # Four simple winning positions.
        for p in range(4):
            buy_ts = NOW - 7200 + p * 500
            _insert_trade(conn, wallet="w5", mint=f"MINT_{p}", buy_sig=f"buy{p}", sell_sig=f"sell{p}",
                          buy_ts=buy_ts, sell_ts=buy_ts + 20, cost=Decimal(1), proceeds=Decimal(2))

        # The fifth/latest position has ten FIFO fragments. Its early five legs
        # make enough profit that the complete position is a win, while its final
        # five fragments are individually losses. A raw rows[-5:] window would
        # incorrectly classify the recent position as losing.
        buy_ts = NOW - 1200
        for i in range(5):
            _insert_trade(conn, wallet="w5", mint="MINT_LATEST", buy_sig="buy-latest", sell_sig=f"win{i}",
                          buy_ts=buy_ts, sell_ts=buy_ts + i, cost=Decimal(1), proceeds=Decimal(3))
        for i in range(5):
            _insert_trade(conn, wallet="w5", mint="MINT_LATEST", buy_sig="buy-latest", sell_sig=f"loss{i}",
                          buy_ts=buy_ts, sell_ts=buy_ts + 100 + i, cost=Decimal(1), proceeds=Decimal("0.5"))
        conn.commit()

    m = guard.quality_metrics(app, "w5", {"lookback_days": "60", "recent_trade_window": "5"})

    assert m["position_closed"] == 5
    assert m["recent_position_closed"] == 5
    assert m["recent_win_rate"] == Decimal(100)
    assert m["recent_fragment_closed"] == 14
