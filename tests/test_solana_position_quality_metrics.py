import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_profit_guard_patch as guard
from learnerbot import solana_sibot as sol

NOW = int(time.time())


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _event(action, mint, signature, ts, token_raw, sol_amount):
    return {
        "action": action,
        "mint": mint,
        "signature": signature,
        "event_ts": ts,
        "token_amount_raw": str(token_raw),
        "sol_amount": str(sol_amount),
        "decimals": 6,
    }


def _persist_reconstruction(conn, wallet, rows, *, exact=True):
    for r in rows:
        conn.execute(
            """INSERT INTO trades(trade_id,wallet,mint,decimals,buy_signature,sell_signature,buy_ts,sell_ts,
                                   token_amount_raw,cost_sol,proceeds_sol,net_sol,hold_seconds,source,updated_at,
                                   position_id,position_closed)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["trade_id"], r["wallet"], r["mint"], r["decimals"], r["buy_signature"], r["sell_signature"],
                r["buy_ts"], r["sell_ts"], r["token_amount_raw"], r["cost_sol"], r["proceeds_sol"], r["net_sol"],
                r["hold_seconds"], r["source"], r["updated_at"], r.get("position_id"), int(r.get("position_closed") or 0),
            ),
        )
    closed = len({str(r.get("position_id")) for r in rows if r.get("position_id") and int(r.get("position_closed") or 0) == 1})
    conn.execute(
        """INSERT INTO history_status(wallet,fetched_at,closed_trades,truncated,error,position_metrics_version)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(wallet) DO UPDATE SET fetched_at=excluded.fetched_at,closed_trades=excluded.closed_trades,
             truncated=excluded.truncated,error=excluded.error,position_metrics_version=excluded.position_metrics_version""",
        (wallet, NOW, closed, 0, "", 1 if exact else 0),
    )
    conn.commit()


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


def test_partial_sell_is_not_marked_as_closed_position():
    rows = sol._match_events(
        "w-open",
        [
            _event("BUY", "MINT", "b", NOW - 100, 100, 10),
            _event("SELL", "MINT", "s", NOW - 50, 40, 5),
        ],
    )
    assert len(rows) == 1
    assert rows[0]["position_id"]
    assert rows[0]["position_closed"] == 0


def test_full_exit_marks_every_fragment_in_cycle_closed():
    rows = sol._match_events(
        "w-close",
        [
            _event("BUY", "MINT", "b", NOW - 100, 100, 10),
            _event("SELL", "MINT", "s1", NOW - 60, 40, 5),
            _event("SELL", "MINT", "s2", NOW - 50, 60, 8),
        ],
    )
    assert len(rows) == 2
    assert len({r["position_id"] for r in rows}) == 1
    assert all(r["position_closed"] == 1 for r in rows)


def test_same_second_exit_then_reentry_is_new_exact_position():
    t = NOW - 500
    rows = sol._match_events(
        "w-tie",
        [
            _event("BUY", "MINT", "a", t - 100, 100, 1),
            _event("SELL", "MINT", "m", t, 100, Decimal("0.5")),
            _event("BUY", "MINT", "z", t, 100, 1),
            _event("SELL", "MINT", "zz", t + 100, 100, 2),
        ],
    )
    assert len(rows) == 2
    assert rows[0]["position_closed"] == rows[1]["position_closed"] == 1
    assert rows[0]["position_id"] != rows[1]["position_id"]


def test_scaled_out_position_uses_one_position_for_all_quality_metrics(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    rows = sol._match_events(
        "w1",
        [
            _event("BUY", "MINT_A", "buyA", buy_ts, 300, 9),
            _event("SELL", "MINT_A", "s1", buy_ts + 100, 100, 2),
            _event("SELL", "MINT_A", "s2", buy_ts + 200, 100, Decimal("2.5")),
            _event("SELL", "MINT_A", "s3", buy_ts + 300, 100, 6),
        ],
    )
    with sol.connect(app) as conn:
        _persist_reconstruction(conn, "w1", rows)

    m = guard.quality_metrics(app, "w1", _cfg())

    assert m["position_metrics_exact"] is True
    assert m["measurement_unit"] == "closed_inventory_positions"
    assert m["fragment_closed"] == 3
    assert m["fragment_win_rate"] == Decimal(100) / Decimal(3)
    assert m["fragment_profit_factor"] == Decimal(2)
    assert m["closed"] == m["position_closed"] == 1
    assert m["wins"] == 1
    assert m["net"] == Decimal("1.5")
    assert m["win_rate"] == Decimal(100)
    assert m["profit_factor"] == Decimal(99)


def test_open_partial_inventory_is_excluded_from_exact_quality(tmp_path):
    app = _app(tmp_path)
    rows = sol._match_events(
        "w-open-db",
        [
            _event("BUY", "MINT", "b", NOW - 1000, 100, 10),
            _event("SELL", "MINT", "s", NOW - 900, 40, 8),
        ],
    )
    with sol.connect(app) as conn:
        _persist_reconstruction(conn, "w-open-db", rows)

    m = guard.quality_metrics(app, "w-open-db", _cfg())
    assert m["fragment_closed"] == 1
    assert m["fragment_win_rate"] == Decimal(100)
    assert m["closed"] == 0
    assert m["position_closed"] == 0
    assert m["net"] == 0


def test_recent_window_is_last_closed_positions_not_last_fragments(tmp_path):
    app = _app(tmp_path)
    rows = []
    base = NOW - 10000
    old_events = [_event("BUY", "OLD", "oldbuy", base, 800, 8)]
    for i in range(8):
        old_events.append(_event("SELL", "OLD", f"old{i:02d}", base + 10 + i, 100, Decimal("0.9")))
    rows.extend(sol._match_events("w4", old_events))
    for i, proceeds in enumerate([2, 2, 2, 2, Decimal("0.5")]):
        ts = base + 1000 + i * 100
        rows.extend(sol._match_events(
            "w4",
            [
                _event("BUY", f"NEW{i}", f"b{i}", ts, 100, 1),
                _event("SELL", f"NEW{i}", f"s{i}", ts + 20, 100, proceeds),
            ],
        ))
    with sol.connect(app) as conn:
        _persist_reconstruction(conn, "w4", rows)

    m = guard.quality_metrics(app, "w4", _cfg(recent_trade_window=5))

    assert m["closed"] == 6
    assert m["fragment_closed"] == 13
    assert m["recent_closed"] == 5
    assert m["recent_win_rate"] == Decimal(80)
    assert m["recent_profit_factor"] == Decimal(8)


def test_fragment_count_cannot_satisfy_minimum_position_evidence(tmp_path):
    app = _app(tmp_path)
    buy_ts = NOW - 3600
    events = [_event("BUY", "MINT_D", "buy", buy_ts, 1000, 10)]
    for i in range(10):
        events.append(_event("SELL", "MINT_D", f"sell{i:02d}", buy_ts + 10 + i, 100, 2))
    rows = sol._match_events("w5", events)
    with sol.connect(app) as conn:
        _persist_reconstruction(conn, "w5", rows)

    cfg = _cfg(min_closed_trades=5)
    m = guard.quality_metrics(app, "w5", cfg)

    assert m["fragment_closed"] == 10
    assert m["closed"] == 1
    assert guard._historical_ok(m, cfg) is False


def test_legacy_rows_keep_old_measurement_only_until_refresh(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO trades(trade_id,wallet,mint,decimals,buy_signature,sell_signature,buy_ts,sell_ts,
                                   token_amount_raw,cost_sol,proceeds_sol,net_sol,hold_seconds,source,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacy", "w-old", "M", 6, "b", "s", NOW - 100, NOW - 50, "100", "1", "2", "1", 50, "TEST", NOW),
        )
        conn.execute(
            """INSERT INTO history_status(wallet,fetched_at,closed_trades,truncated,error,position_metrics_version)
               VALUES(?,?,?,?,?,?)""",
            ("w-old", NOW, 1, 0, "", 0),
        )
        conn.commit()

    m = guard.quality_metrics(app, "w-old", _cfg())
    assert m["position_metrics_exact"] is False
    assert m["measurement_unit"] == "legacy_fragments_pending_refresh"
    assert m["closed"] == 1
    assert m["position_closed"] == 0
