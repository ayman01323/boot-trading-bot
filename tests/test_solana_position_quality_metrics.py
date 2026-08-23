import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_profit_guard_patch as guard
from learnerbot import solana_sibot as sol

NOW = int(time.time())


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _insert_trade(conn, *, wallet, mint, buy_sig, sell_sig, buy_ts, sell_ts, cost, proceeds):
    net = Decimal(str(proceeds)) - Decimal(str(cost))
    trade_id = f"{wallet}|{mint}|{buy_sig}|{sell_sig}"
    conn.execute(
        """INSERT INTO trades(trade_id,wallet,mint,decimals,buy_signature,sell_signature,buy_ts,sell_ts,
                               token_amount_raw,cost_sol,proceeds_sol,net_sol,hold_seconds,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trade_id, wallet, mint, 6, buy_sig, sell_sig, buy_ts, sell_ts,
            "1000", str(cost), str(proceeds), str(net), max(0, sell_ts - buy_ts), "TEST", sell_ts,
        ),
    )


def _cfg(**overrides):
    cfg = {
        "lookback_days": "60",
        "recent_trade_window": "5",
        "require_complete_history": "false",
        "min_closed_trades": "1",
        "min_win_rate_pct": "0",
        "min_profit_factor": "0",
        "max_leader_drawdown_pct": "100",
        "min_recent_win_rate_pct": "0",
        "min_recent_profit_factor": "0",
    }
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


def test_scaled_out_position_uses_one_position_for_all_quality_metrics(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s1",
                      buy_ts=buy_ts, sell_ts=buy_ts + 100, cost=3, proceeds=2)
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s2",
                      buy_ts=buy_ts, sell_ts=buy_ts + 200, cost=3, proceeds=Decimal("2.5"))
        _insert_trade(conn, wallet="w1", mint="MINT_A", buy_sig="buyA", sell_sig="s3",
                      buy_ts=buy_ts, sell_ts=buy_ts + 300, cost=3, proceeds=6)
        conn.commit()

    m = guard.quality_metrics(app, "w1", _cfg())

    assert m["fragment_closed"] == 3
    assert m["fragment_win_rate"] == Decimal(100) / Decimal(3)
    assert m["fragment_profit_factor"] == Decimal(2)
    assert m["closed"] == m["position_closed"] == 1
    assert m["wins"] == 1
    assert m["net"] == Decimal("1.5")
    assert m["win_rate"] == Decimal(100)
    assert m["profit_factor"] == Decimal(99)


def test_reentry_after_full_exit_is_a_new_position(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy1", sell_sig="sell1",
                      buy_ts=NOW - 7200, sell_ts=NOW - 7100, cost=2, proceeds=1)
        _insert_trade(conn, wallet="w2", mint="MINT_B", buy_sig="buy2", sell_sig="sell2",
                      buy_ts=NOW - 3600, sell_ts=NOW - 3500, cost=2, proceeds=4)
        conn.commit()

    m = guard.quality_metrics(app, "w2", _cfg())

    assert m["closed"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 1
    assert m["win_rate"] == Decimal(50)
    assert m["profit_factor"] == Decimal(2)


def test_same_second_exit_then_reentry_uses_signature_tie_break(tmp_path):
    app = _app(tmp_path)
    t = NOW - 1800
    with sol.connect(app) as conn:
        # solana_sibot orders same-second events by signature. sell=m happens
        # before the new buy=z, so these are separate positions despite equal ts.
        _insert_trade(conn, wallet="w3", mint="MINT_C", buy_sig="a", sell_sig="m",
                      buy_ts=t - 100, sell_ts=t, cost=1, proceeds=Decimal("0.5"))
        _insert_trade(conn, wallet="w3", mint="MINT_C", buy_sig="z", sell_sig="zz",
                      buy_ts=t, sell_ts=t + 100, cost=1, proceeds=2)
        conn.commit()

    m = guard.quality_metrics(app, "w3", _cfg())
    assert m["closed"] == 2
    assert m["win_rate"] == Decimal(50)


def test_recent_window_is_last_positions_not_last_fragments(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        base = NOW - 10000
        # Old fragmented position: eight losing fragments but one old position.
        for i in range(8):
            _insert_trade(conn, wallet="w4", mint="OLD", buy_sig="oldbuy", sell_sig=f"old{i:02d}",
                          buy_ts=base, sell_ts=base + 10 + i, cost=1, proceeds=Decimal("0.9"))
        # Five newer positions: W, W, W, W, L => recent position win rate 80%.
        for i, proceeds in enumerate([2, 2, 2, 2, Decimal("0.5")]):
            ts = base + 1000 + i * 100
            _insert_trade(conn, wallet="w4", mint=f"NEW{i}", buy_sig=f"b{i}", sell_sig=f"s{i}",
                          buy_ts=ts, sell_ts=ts + 20, cost=1, proceeds=proceeds)
        conn.commit()

    m = guard.quality_metrics(app, "w4", _cfg(recent_trade_window=5))

    assert m["closed"] == 6
    assert m["fragment_closed"] == 13
    assert m["recent_closed"] == 5
    assert m["recent_win_rate"] == Decimal(80)
    assert m["recent_profit_factor"] == Decimal(8)


def test_fragment_count_cannot_satisfy_minimum_position_evidence(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    with sol.connect(app) as conn:
        for i in range(10):
            _insert_trade(conn, wallet="w5", mint="MINT_D", buy_sig="buy", sell_sig=f"sell{i:02d}",
                          buy_ts=buy_ts, sell_ts=buy_ts + 10 + i, cost=1, proceeds=2)
        conn.commit()

    cfg = _cfg(min_closed_trades=5)
    m = guard.quality_metrics(app, "w5", cfg)

    assert m["fragment_closed"] == 10
    assert m["closed"] == 1
    assert guard._historical_ok(m, cfg) is False


def test_lookback_boundary_keeps_complete_position_not_partial_fragments(tmp_path):
    app = _app(tmp_path)
    cutoffish = NOW - 86400
    with sol.connect(app) as conn:
        # One position straddles a one-day lookback. The early loss fragment must
        # remain attached because the position itself closes inside the window.
        _insert_trade(conn, wallet="w6", mint="MINT_E", buy_sig="buy", sell_sig="early",
                      buy_ts=cutoffish - 200, sell_ts=cutoffish - 50, cost=1, proceeds=0)
        _insert_trade(conn, wallet="w6", mint="MINT_E", buy_sig="buy", sell_sig="late",
                      buy_ts=cutoffish - 200, sell_ts=cutoffish + 50, cost=1, proceeds=3)
        conn.commit()

    m = guard.quality_metrics(app, "w6", _cfg(lookback_days=1))

    assert m["closed"] == 1
    assert m["net"] == Decimal(1)
    assert m["win_rate"] == Decimal(100)
