from __future__ import annotations

import base64
import time
from contextlib import closing
from decimal import Decimal

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol

# CloseAccount is instruction discriminator 9 for the SPL Token interface.
_CLOSE_ACCOUNT = bytes([9])
_ALLOWED_TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token Program
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022 Program
}

_RECLAIM_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_position_created_token_accounts(
  position_id TEXT NOT NULL,
  account_pubkey TEXT NOT NULL,
  program_id TEXT NOT NULL,
  entry_lamports TEXT NOT NULL DEFAULT '0',
  created_at INTEGER NOT NULL,
  closed_at INTEGER,
  close_signature TEXT,
  reclaimed_lamports TEXT NOT NULL DEFAULT '0',
  PRIMARY KEY(position_id,account_pubkey)
);
CREATE INDEX IF NOT EXISTS idx_sol_reclaim_position
  ON live_position_created_token_accounts(position_id,closed_at);
"""

_PREV_BUY = _exec.SolanaLiveExecutor.buy
_PREV_INSERT = _live._insert_live_position


def _ensure_schema(conn):
    conn.executescript(_RECLAIM_SCHEMA)


def _token_accounts(executor, mint):
    """Return token accounts for this wallet/mint, or None when RPC is uncertain."""
    result = _sol._rpc(
        executor.app,
        "getTokenAccountsByOwner",
        [
            str(executor.address),
            {"mint": str(mint)},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    )
    if not isinstance(result, dict):
        return None
    out = {}
    for row in result.get("value") or []:
        try:
            pubkey = str(row.get("pubkey") or "")
            account = row.get("account") or {}
            program_id = str(account.get("owner") or "")
            info = (((account.get("data") or {}).get("parsed") or {}).get("info") or {})
            amount = int(((info.get("tokenAmount") or {}).get("amount")) or 0)
            lamports = int(account.get("lamports") or 0)
        except Exception:
            continue
        if pubkey:
            out[pubkey] = {
                "pubkey": pubkey,
                "program_id": program_id,
                "amount": amount,
                "lamports": lamports,
            }
    return out


def _buy_track_created_accounts(self, output_mint, amount_sol, reserve_sol):
    before = None
    try:
        before = _token_accounts(self, output_mint)
    except Exception:
        before = None

    result = dict(_PREV_BUY(self, output_mint, amount_sol, reserve_sol) or {})

    after = None
    try:
        after = _token_accounts(self, output_mint)
    except Exception:
        after = None

    created = []
    # Never infer account creation from a failed/unavailable pre-snapshot.
    if before is not None and after is not None:
        for pubkey in sorted(set(after) - set(before)):
            row = dict(after[pubkey])
            if row.get("program_id") in _ALLOWED_TOKEN_PROGRAMS:
                created.append(row)
    result["bot_created_output_token_accounts"] = created
    result["token_account_creation_reconciled"] = before is not None and after is not None
    return result


def _insert_with_reclaim_tracking(app, tid, rank, event, trade, allocation, cfg):
    pid, out_raw, entry_cost = _PREV_INSERT(app, tid, rank, event, trade, allocation, cfg)
    created = list((trade or {}).get("bot_created_output_token_accounts") or [])
    if created:
        now = int(time.time())
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _ensure_schema(conn)
            for row in created:
                program_id = str(row.get("program_id") or "")
                pubkey = str(row.get("pubkey") or "")
                if not pubkey or program_id not in _ALLOWED_TOKEN_PROGRAMS:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO live_position_created_token_accounts(
                         position_id,account_pubkey,program_id,entry_lamports,created_at
                       ) VALUES(?,?,?,?,?)""",
                    (pid, pubkey, program_id, str(int(row.get("lamports") or 0)), now),
                )
            conn.commit()
    return pid, out_raw, entry_cost


def _tracked_accounts(app, position_id):
    with closing(_sol.connect(app)) as conn:
        _ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            """SELECT * FROM live_position_created_token_accounts
               WHERE position_id=? AND closed_at IS NULL ORDER BY account_pubkey""",
            (str(position_id),),
        ).fetchall()]


def _confirm_signature(app, signature, timeout_seconds=12):
    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            result = _sol._rpc(
                app,
                "getSignatureStatuses",
                [[str(signature)], {"searchTransactionHistory": True}],
            ) or {}
            values = result.get("value") or []
            status = values[0] if values else None
            if status:
                if status.get("err") is not None:
                    return False
                if str(status.get("confirmationStatus") or "") in {"confirmed", "finalized"}:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _close_created_empty_accounts(executor, position_id, mint):
    """Close only bot-created, still-empty accounts and return proven wallet refund."""
    tracked = _tracked_accounts(executor.app, position_id)
    if not tracked:
        return {"reclaimed_lamports": 0, "signature": "", "accounts": []}

    try:
        current = _token_accounts(executor, mint)
    except Exception:
        current = None
    if current is None:
        return {"reclaimed_lamports": 0, "signature": "", "accounts": []}

    eligible = []
    for row in tracked:
        pubkey = str(row.get("account_pubkey") or "")
        live = current.get(pubkey)
        # If the SELL itself already closed the account, its rent refund is already
        # part of the SELL wallet delta. Mark it reconciled without double counting.
        if live is None:
            with _sol._DB_LOCK, closing(_sol.connect(executor.app)) as conn:
                _ensure_schema(conn)
                conn.execute(
                    """UPDATE live_position_created_token_accounts
                       SET closed_at=?,close_signature='IN_SWAP',reclaimed_lamports='0'
                       WHERE position_id=? AND account_pubkey=? AND closed_at IS NULL""",
                    (int(time.time()), str(position_id), pubkey),
                )
                conn.commit()
            continue
        if int(live.get("amount") or 0) != 0:
            continue
        program_id = str(live.get("program_id") or "")
        if program_id not in _ALLOWED_TOKEN_PROGRAMS:
            continue
        if program_id != str(row.get("program_id") or ""):
            continue
        eligible.append((row, live))

    if not eligible:
        return {"reclaimed_lamports": 0, "signature": "", "accounts": []}

    before = executor.native_balance_lamports()
    kp = Keypair.from_bytes(bytes(executor.keypair))
    owner = kp.pubkey()
    if str(owner) != str(executor.address):
        raise _exec.SolanaLiveError("Solana token-account reclaim signer does not match execution wallet")

    block = _sol._rpc(executor.app, "getLatestBlockhash", [{"commitment": "confirmed"}]) or {}
    blockhash_text = str((block.get("value") or {}).get("blockhash") or "")
    if not blockhash_text:
        raise _exec.SolanaLiveError("Cannot reclaim token-account rent: no recent blockhash")
    blockhash = Hash.from_string(blockhash_text)

    instructions = []
    accounts = []
    for row, live in eligible[:4]:
        account = Pubkey.from_string(str(row["account_pubkey"]))
        program = Pubkey.from_string(str(live["program_id"]))
        instructions.append(
            Instruction(
                program,
                _CLOSE_ACCOUNT,
                [
                    AccountMeta(account, False, True),
                    AccountMeta(owner, False, True),
                    AccountMeta(owner, True, False),
                ],
            )
        )
        accounts.append(str(row["account_pubkey"]))

    message = MessageV0.try_compile(owner, instructions, [], blockhash)
    tx = VersionedTransaction(message, [kp])
    raw_b64 = base64.b64encode(bytes(tx)).decode("ascii")
    signature = str(_sol._rpc(
        executor.app,
        "sendTransaction",
        [
            raw_b64,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "confirmed",
                "maxRetries": 3,
            },
        ],
    ) or "")
    if not signature or not _confirm_signature(executor.app, signature):
        return {"reclaimed_lamports": 0, "signature": signature, "accounts": []}

    after = executor.native_balance_lamports()
    reclaimed = max(0, int(after) - int(before))
    now = int(time.time())
    with _sol._DB_LOCK, closing(_sol.connect(executor.app)) as conn:
        _ensure_schema(conn)
        for account in accounts:
            conn.execute(
                """UPDATE live_position_created_token_accounts
                   SET closed_at=?,close_signature=?,reclaimed_lamports=?
                   WHERE position_id=? AND account_pubkey=? AND closed_at IS NULL""",
                (now, signature, str(reclaimed), str(position_id), account),
            )
        conn.commit()
    return {"reclaimed_lamports": reclaimed, "signature": signature, "accounts": accounts}


def _close_bound_live_with_reclaim(app, tid, position, fraction: Decimal, reason: str):
    executor, actual = _binding._resolve_executor(app, tid, position)
    old_raw = max(1, _sol._int(position.get("token_amount_raw"), 0))
    f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    planned = max(1, int(Decimal(old_raw) * f))
    sell_raw = min(planned, int(actual))
    if sell_raw <= 0:
        _binding._quarantine(app, position, "resolved wallet has no sellable matching token")

    trade = executor.sell(position["mint"], sell_raw)
    out_lamports = int(trade.get("totalOutputAmount") or trade.get("outputAmountResult") or 0)
    delta_raw = trade.get("wallet_delta_lamports")
    try:
        delta = int(delta_raw) if delta_raw is not None else 0
    except Exception:
        delta = 0
    cfg = _sol.settings(app)
    if delta > 0:
        swap_proceeds = Decimal(delta) / Decimal(1_000_000_000)
    else:
        swap_proceeds = (
            Decimal(out_lamports) / Decimal(1_000_000_000)
            - _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002")
        )

    old_cost = _sol._dec(position.get("entry_cost_sol"), 0)
    cost_fraction = old_cost * Decimal(sell_raw) / Decimal(old_raw)
    remaining = max(0, old_raw - sell_raw)
    remaining_cost = max(Decimal(0), old_cost - cost_fraction)
    closed = remaining <= max(1, int(old_raw * .001)) or f >= Decimal("0.999")

    reclaim = {"reclaimed_lamports": 0, "signature": "", "accounts": []}
    if closed:
        try:
            reclaim = _close_created_empty_accounts(
                executor,
                str(position.get("position_id") or ""),
                str(position.get("mint") or ""),
            )
        except Exception as exc:
            # A reclaim failure must never turn a successful SELL into a failed exit.
            print("[solana-token-reclaim]", type(exc).__name__, exc)

    reclaimed_sol = Decimal(int(reclaim.get("reclaimed_lamports") or 0)) / Decimal(1_000_000_000)
    proceeds = swap_proceeds + reclaimed_sol
    net = proceeds - cost_fraction
    realised = _sol._dec(position.get("realised_net_sol"), 0) + net
    now = int(time.time())
    sig = str(trade.get("signature") or "")

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,realised_net_sol=?,exit_signature=?,exit_reason=?,closed_at=?,
                                    status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
            (
                str(0 if closed else remaining),
                str(0 if closed else remaining_cost),
                str(realised),
                sig,
                reason,
                now if closed else None,
                "CLOSED" if closed else "OPEN",
                0 if closed else int(position.get("leader_exit_pending") or 0),
                now,
                position["position_id"],
            ),
        )
        conn.commit()

    reclaim_line = (
        f"Rent reclaimed: <b>{reclaimed_sol:.9f} SOL</b>\n" if reclaimed_sol > 0 else ""
    )
    _live._notify(
        app,
        tid,
        f"✅ <b>Solana LIVE SELL confirmed</b>\n"
        f"Reason: <code>{reason}</code>\n"
        f"Wallet: <code>{executor.address[:8]}…{executor.address[-6:]}</code>\n"
        f"Swap wallet proceeds: <b>{swap_proceeds:.9f} SOL</b>\n"
        f"{reclaim_line}"
        f"Realised net P&L: <b>{net:+.9f} SOL</b>\n"
        f"TX: <code>{sig}</code>"
        + (f"\nRent reclaim TX: <code>{reclaim.get('signature')}</code>" if reclaim.get("signature") else ""),
    )
    trade = dict(trade or {})
    trade["rent_reclaimed_lamports"] = int(reclaim.get("reclaimed_lamports") or 0)
    trade["rent_reclaim_signature"] = str(reclaim.get("signature") or "")
    return {
        "closed": closed,
        "net_sol": net,
        "signature": sig,
        "reason": reason,
        "trade": trade,
        "rent_reclaimed_sol": reclaimed_sol,
    }


def install():
    if getattr(_exec.SolanaLiveExecutor, "_token_account_reclaim_patch", False):
        return
    _exec.SolanaLiveExecutor.buy = _buy_track_created_accounts
    _live._insert_live_position = _insert_with_reclaim_tracking
    _binding._close_bound_live = _close_bound_live_with_reclaim
    _live._close_live = _close_bound_live_with_reclaim
    _exec.SolanaLiveExecutor._token_account_reclaim_patch = True
    print(
        "[solana-token-reclaim] bot_created_only=true zero_balance_only=true "
        "refund_in_realised_pnl=true"
    )


install()
