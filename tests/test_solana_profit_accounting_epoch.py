from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_profit_accounting_epoch_patch as epoch
from learnerbot import solana_sibot as sol


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _closed(app, pid, closed_at, pnl):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,exit_signature,exit_reason,closed_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, "123", "leader", 1, pid + "mint", "LIVE", "CLOSED", "0", "0", 1, "buy", "0", "0", 1,
             "0", "0", 0.0, 0.0, 0, str(pnl), "sell", "TEST", int(closed_at), int(closed_at)),
        )
        conn.commit()


def test_pre_epoch_losses_do_not_poison_corrected_guard(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        sol._set_state(conn, epoch._EPOCH_STATE_KEY, 100)
    _closed(app, "oldloss", 90, Decimal("-1"))
    _closed(app, "newwin", 110, Decimal("0.2"))

    metrics = epoch._copied_metrics_corrected(app, "123", "leader")
    assert metrics["closed"] == 1
    assert metrics["win_rate"] == Decimal("100")
    assert metrics["profit_factor"] == Decimal("99")
    assert metrics["consecutive_losses"] == 0


def test_pending_rent_reclaim_is_not_learned_as_loss(tmp_path):
    app = _app(tmp_path)
    with sol.connect(app) as conn:
        sol._set_state(conn, epoch._EPOCH_STATE_KEY, 100)
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS live_position_created_token_accounts(
                 position_id TEXT NOT NULL,
                 account_pubkey TEXT NOT NULL,
                 program_id TEXT NOT NULL,
                 entry_lamports TEXT NOT NULL DEFAULT '0',
                 created_at INTEGER NOT NULL,
                 closed_at INTEGER,
                 close_signature TEXT,
                 reclaimed_lamports TEXT NOT NULL DEFAULT '0',
                 PRIMARY KEY(position_id,account_pubkey)
               );"""
        )
        conn.commit()
    _closed(app, "pending", 110, Decimal("-0.0018"))
    _closed(app, "settledwin", 120, Decimal("0.0002"))
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO live_position_created_token_accounts(
                 position_id,account_pubkey,program_id,entry_lamports,created_at,closed_at
               ) VALUES(?,?,?,?,?,NULL)""",
            ("pending", "ata", "TokenProgram", "1844400", 100),
        )
        conn.commit()

    metrics = epoch._copied_metrics_corrected(app, "123", "leader")
    assert metrics["closed"] == 1
    assert metrics["win_rate"] == Decimal("100")
    assert metrics["profit_factor"] == Decimal("99")


def test_old_suspend_state_is_cleared_once(tmp_path):
    app = _app(tmp_path)
    key = "sol_profit_guard_suspend:123:leader"
    with sol.connect(app) as conn:
        sol._set_state(conn, key, '{"until":9999999999,"latest_closed_at":50}')
    epoch._clear_old_suspend_states(app)
    with sol.connect(app) as conn:
        raw = sol._state(conn, key, "")
        marker = sol._state(conn, "solana_corrected_live_pnl_epoch_v2_suspend_cleanup", "")
    assert '"until": 0' in raw
    assert marker
