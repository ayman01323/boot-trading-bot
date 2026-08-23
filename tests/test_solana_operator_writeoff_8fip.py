from __future__ import annotations

import sqlite3
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_live_position_scope_fix_patch as scope
from learnerbot import solana_operator_writeoff_8fip_migration as writeoff
from learnerbot import solana_profit_accounting_epoch_patch as epoch
from learnerbot import solana_sibot as sol


MINT = writeoff.TARGET_MINT
PID = writeoff.TARGET_POSITION_ID


def _minimal_writeoff_db(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    path = data / "solana_sibot.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE positions(
             position_id TEXT PRIMARY KEY,
             telegram_id TEXT NOT NULL,
             leader_wallet TEXT,
             mint TEXT NOT NULL,
             mode TEXT NOT NULL,
             status TEXT NOT NULL,
             token_amount_raw TEXT NOT NULL,
             entry_cost_sol TEXT NOT NULL,
             current_exit_sol TEXT NOT NULL DEFAULT '0',
             unrealised_net_sol TEXT NOT NULL DEFAULT '0',
             unrealised_pct REAL NOT NULL DEFAULT 0,
             realised_net_sol TEXT NOT NULL DEFAULT '0',
             exit_signature TEXT,
             exit_reason TEXT,
             closed_at INTEGER,
             leader_exit_pending INTEGER NOT NULL DEFAULT 0,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO positions(
             position_id,telegram_id,leader_wallet,mint,mode,status,token_amount_raw,
             entry_cost_sol,current_exit_sol,unrealised_net_sol,unrealised_pct,
             realised_net_sol,exit_signature,leader_exit_pending,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            PID,
            "5923828381",
            "leader",
            MINT,
            "LIVE",
            "OPEN",
            "87405554",
            "0.005",
            "0",
            "-0.005",
            -100.0,
            "0.0001",
            "prior-signature",
            1,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return path


def test_exact_writeoff_closes_accounting_without_fabricating_sell(tmp_path):
    path = _minimal_writeoff_db(tmp_path)

    assert writeoff.apply(tmp_path) is True

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM positions WHERE position_id=?", (PID,)).fetchone()
    audit = conn.execute(
        "SELECT * FROM operator_position_writeoffs WHERE writeoff_id=?",
        (writeoff.WRITEOFF_ID,),
    ).fetchone()
    conn.close()

    assert row["status"] == "CLOSED"
    assert row["token_amount_raw"] == "87405554"  # on-chain inventory is not falsified
    assert row["entry_cost_sol"] == "0"
    assert Decimal(row["realised_net_sol"]) == Decimal("-0.0049")
    assert row["exit_signature"] == "prior-signature"  # no fake write-off transaction
    assert row["exit_reason"].startswith("OPERATOR_WRITE_OFF_ZERO_RECOVERY:")
    assert row["leader_exit_pending"] == 0
    assert row["closed_at"] is not None

    assert audit["mint"] == MINT
    assert audit["position_id"] == PID
    assert audit["recorded_token_amount_raw"] == "87405554"
    assert Decimal(audit["remaining_cost_sol"]) == Decimal("0.005")
    assert Decimal(audit["realised_net_after_sol"]) == Decimal("-0.0049")
    assert audit["on_chain_disposal"] == 0
    assert (tmp_path / "data" / writeoff.MARKER_NAME).exists()


def test_writeoff_is_idempotent_and_cannot_double_realise_loss(tmp_path):
    path = _minimal_writeoff_db(tmp_path)
    assert writeoff.apply(tmp_path) is True
    assert writeoff.apply(tmp_path) is False

    conn = sqlite3.connect(path)
    realised = conn.execute(
        "SELECT realised_net_sol FROM positions WHERE position_id=?", (PID,)
    ).fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM operator_position_writeoffs").fetchone()[0]
    conn.close()
    assert Decimal(realised) == Decimal("-0.0049")
    assert count == 1


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "csv")


def _insert_full_position(app, *, status="CLOSED", exit_reason=writeoff.REASON, realised="-0.005"):
    with sol.connect(app) as conn:
        conn.execute(
            """INSERT INTO positions(
                 position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                 entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                 signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                 leader_exit_pending,realised_net_sol,exit_signature,exit_reason,closed_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                PID,
                "5923828381",
                "leader-wallet",
                1,
                MINT,
                "LIVE",
                status,
                "87405554",
                "0",
                10,
                "buy-sig",
                "0.005",
                "87405554",
                1,
                "0",
                "0",
                0.0,
                0.0,
                0,
                realised,
                None,
                exit_reason,
                100,
                100,
            ),
        )
        conn.commit()


def test_written_off_mint_remains_blocked_from_fresh_live_entry(tmp_path):
    app = _app(tmp_path)
    _insert_full_position(app)
    blocker = scope._open_live_position(app, "5923828381", MINT)
    assert blocker is not None
    assert blocker["status"] == "CLOSED"
    assert blocker["exit_reason"].startswith("OPERATOR_WRITE_OFF_ZERO_RECOVERY:")


def test_written_off_loss_counts_even_while_token_account_rent_is_unreclaimed(tmp_path):
    app = _app(tmp_path)
    _insert_full_position(app)
    with sol.connect(app) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS live_position_created_token_accounts(
                 position_id TEXT NOT NULL,
                 closed_at INTEGER
               )"""
        )
        conn.execute(
            "INSERT INTO live_position_created_token_accounts(position_id,closed_at) VALUES(?,NULL)",
            (PID,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
            (epoch._EPOCH_STATE_KEY, "1"),
        )
        conn.commit()

    metrics = epoch._copied_metrics_corrected(app, "5923828381", "leader-wallet")
    assert metrics["closed"] == 1
    assert metrics["losses"] == 1
    assert metrics["gross_loss_sol"] == Decimal("0.005")
    assert metrics["net_sol"] == Decimal("-0.005")
