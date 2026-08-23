from __future__ import annotations

import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path


TARGET_MINT = "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV"
TARGET_POSITION_ID = "07d9f95e7dbb77288b2d4abca53e3949"
WRITEOFF_ID = "operator-writeoff-20260823-8fip"
MARKER_NAME = ".solana_operator_writeoff_8fip_20260823_applied"
REASON = (
    "OPERATOR_WRITE_OFF_ZERO_RECOVERY: owner instructed write-off; "
    "on-chain token remains in wallet; no SELL submitted; future same-mint LIVE entry blocked"
)

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_position_writeoffs(
  writeoff_id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  telegram_id TEXT NOT NULL,
  mint TEXT NOT NULL,
  recorded_token_amount_raw TEXT NOT NULL,
  remaining_cost_sol TEXT NOT NULL,
  realised_net_before_sol TEXT NOT NULL,
  realised_net_after_sol TEXT NOT NULL,
  reason TEXT NOT NULL,
  on_chain_disposal INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_position_writeoffs_mint
  ON operator_position_writeoffs(mint,created_at DESC);
"""


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def apply(root: Path | None = None) -> bool:
    """Write off exactly one owner-authorised stuck LIVE position, idempotently.

    This is an accounting close only. It never signs, broadcasts, swaps, burns or
    transfers the token. The recorded token quantity is deliberately preserved so
    the database continues to show that on-chain dust/stuck inventory still exists.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    db_path = root / "data" / "solana_sibot.sqlite3"
    marker = root / "data" / MARKER_NAME
    if not db_path.exists():
        print(f"[solana-operator-writeoff] pending=true reason=db_missing mint={TARGET_MINT}")
        return False

    now = int(time.time())
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_AUDIT_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT * FROM operator_position_writeoffs WHERE writeoff_id=?",
            (WRITEOFF_ID,),
        ).fetchone()
        if existing:
            conn.commit()
            marker.parent.mkdir(parents=True, exist_ok=True)
            if not marker.exists():
                marker.write_text(
                    f"writeoff_id={WRITEOFF_ID}\nposition_id={TARGET_POSITION_ID}\nmint={TARGET_MINT}\n",
                    encoding="utf-8",
                )
            print(
                "[solana-operator-writeoff] already_applied=true "
                f"position={TARGET_POSITION_ID} mint={TARGET_MINT}"
            )
            return False

        row = conn.execute(
            """SELECT * FROM positions
               WHERE position_id=? AND mint=? AND mode='LIVE'
                 AND status IN ('OPEN','RECONCILE_REQUIRED')""",
            (TARGET_POSITION_ID, TARGET_MINT),
        ).fetchone()
        if not row:
            current = conn.execute(
                "SELECT status,mint FROM positions WHERE position_id=?",
                (TARGET_POSITION_ID,),
            ).fetchone()
            conn.rollback()
            status = str(current["status"] if current else "MISSING")
            mint = str(current["mint"] if current else "")
            print(
                "[solana-operator-writeoff] applied=false "
                f"position={TARGET_POSITION_ID} expected_mint={TARGET_MINT} "
                f"current_status={status} current_mint={mint}"
            )
            return False

        remaining_cost = max(Decimal(0), _dec(row["entry_cost_sol"]))
        realised_before = _dec(row["realised_net_sol"])
        realised_after = realised_before - remaining_cost
        recorded_raw = str(row["token_amount_raw"] or "0")

        cur = conn.execute(
            """UPDATE positions
               SET status='CLOSED',
                   entry_cost_sol='0',
                   current_exit_sol='0',
                   unrealised_net_sol='0',
                   unrealised_pct=0,
                   realised_net_sol=?,
                   exit_reason=?,
                   closed_at=?,
                   leader_exit_pending=0,
                   updated_at=?
               WHERE position_id=? AND mint=? AND mode='LIVE'
                 AND status IN ('OPEN','RECONCILE_REQUIRED')""",
            (
                str(realised_after),
                REASON,
                now,
                now,
                TARGET_POSITION_ID,
                TARGET_MINT,
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            print("[solana-operator-writeoff] applied=false reason=concurrent_state_change")
            return False

        conn.execute(
            """INSERT INTO operator_position_writeoffs(
                 writeoff_id,position_id,telegram_id,mint,recorded_token_amount_raw,
                 remaining_cost_sol,realised_net_before_sol,realised_net_after_sol,
                 reason,on_chain_disposal,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                WRITEOFF_ID,
                TARGET_POSITION_ID,
                str(row["telegram_id"] or ""),
                TARGET_MINT,
                recorded_raw,
                str(remaining_cost),
                str(realised_before),
                str(realised_after),
                REASON,
                0,
                now,
            ),
        )
        conn.commit()

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "\n".join(
                [
                    f"writeoff_id={WRITEOFF_ID}",
                    f"position_id={TARGET_POSITION_ID}",
                    f"mint={TARGET_MINT}",
                    f"recorded_token_amount_raw={recorded_raw}",
                    f"remaining_cost_sol={remaining_cost}",
                    f"realised_net_before_sol={realised_before}",
                    f"realised_net_after_sol={realised_after}",
                    "on_chain_disposal=0",
                    f"created_at={now}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(
            "[solana-operator-writeoff] applied=true accounting_close=true on_chain_disposal=false "
            f"position={TARGET_POSITION_ID} mint={TARGET_MINT} "
            f"remaining_cost_sol={remaining_cost} realised_after_sol={realised_after}"
        )
        return True
    finally:
        conn.close()
