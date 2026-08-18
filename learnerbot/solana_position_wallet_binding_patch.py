from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_sibot as _sol
from .solana_wallet_store import SolanaWalletStore

_PREV_SWAP = _exec.SolanaLiveExecutor.swap
_PREV_INSERT = _live._insert_live_position

_BINDING_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_position_wallets(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  wallet_id TEXT NOT NULL,
  wallet_address TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_position_wallet_user
  ON live_position_wallets(telegram_id,wallet_id);
"""


class SolanaPositionReconcileRequired(_exec.SolanaLiveError):
    """The DB says OPEN but no safe signing wallet can presently supply the token."""


def _ensure_schema(conn):
    conn.executescript(_BINDING_SCHEMA)


def _bind(app, position_id, tid, wallet_id, address, source="ENTRY"):
    if not position_id or not wallet_id or not address:
        return
    now = int(time.time())
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure_schema(conn)
        conn.execute(
            """INSERT INTO live_position_wallets(
                 position_id,telegram_id,wallet_id,wallet_address,source,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(position_id) DO UPDATE SET
                 telegram_id=excluded.telegram_id,
                 wallet_id=excluded.wallet_id,
                 wallet_address=excluded.wallet_address,
                 source=excluded.source,
                 updated_at=excluded.updated_at""",
            (str(position_id), str(tid), str(wallet_id), str(address), str(source), now, now),
        )
        conn.commit()


def _binding(app, position_id):
    with closing(_sol.connect(app)) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM live_position_wallets WHERE position_id=?",
            (str(position_id),),
        ).fetchone()
        return dict(row) if row else None


def _quarantine(app, position, reason):
    """Stop repeated exit attempts without pretending the position was sold."""
    now = int(time.time())
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """UPDATE positions
               SET status='RECONCILE_REQUIRED',exit_reason=?,leader_exit_pending=0,updated_at=?
               WHERE position_id=? AND status='OPEN'""",
            (str(reason)[:500], now, str(position.get("position_id") or "")),
        )
        conn.commit()
    raise SolanaPositionReconcileRequired(
        f"Position quarantined as RECONCILE_REQUIRED: {reason}. "
        "No further automatic exit attempts will be made until the wallet/token state is reconciled."
    )


def _token_balance_for_address(app, address, mint):
    result = _sol._rpc(
        app,
        "getTokenAccountsByOwner",
        [str(address), {"mint": str(mint)}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
    ) or {}
    total = 0
    for row in result.get("value") or []:
        try:
            total += int(row["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
        except Exception:
            continue
    return max(0, total)


def _wallet_rows(store, tid):
    try:
        return list(store.list_wallets(tid, enabled_only=False))
    except Exception:
        return []


def _wallet_is_enabled(row):
    return str(row.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}


def _wallet_has_key(store, tid, row):
    # has_private_key() intentionally ignores disabled wallets; that is desirable
    # for execution. A disabled/public wallet may still be reported as holding the
    # token but is never silently re-enabled or used for signing.
    if not _wallet_is_enabled(row):
        return False
    return bool(store.has_private_key(tid, row.get("wallet_id")))


def _resolve_executor(app, tid, position):
    """Find the wallet that actually holds this position's token and pin it."""
    store = SolanaWalletStore(app.csv_dir, app.data_dir)
    pid = str(position.get("position_id") or "")
    mint = str(position.get("mint") or "")
    bound = _binding(app, pid)
    rows = _wallet_rows(store, tid)
    by_id = {str(r.get("wallet_id") or ""): r for r in rows}

    # First preference is always the entry-wallet binding. An active-wallet switch
    # must never redirect an existing position's exit.
    if bound:
        row = by_id.get(str(bound.get("wallet_id") or ""))
        if row:
            bal = _token_balance_for_address(app, row.get("address"), mint)
            if bal > 0:
                if not _wallet_has_key(store, tid, row):
                    _quarantine(app, position, "entry wallet holds the token but is disabled or not SIGNING READY")
                return _exec.SolanaLiveExecutor(app, tid, wallet_id=row.get("wallet_id")), bal

    # Legacy positions pre-date wallet binding. Search every registered address.
    # This is also a recovery path if a user manually transferred the token from
    # the entry wallet to another registered wallet.
    holders = []
    for row in rows:
        address = str(row.get("address") or "")
        if not address:
            continue
        try:
            bal = _token_balance_for_address(app, address, mint)
        except Exception:
            continue
        if bal > 0:
            holders.append((row, bal))

    signing_holders = [(r, b) for r, b in holders if _wallet_has_key(store, tid, r)]
    if len(signing_holders) == 1:
        row, bal = signing_holders[0]
        _bind(app, pid, tid, row.get("wallet_id"), row.get("address"), "LEGACY_RESOLVED")
        return _exec.SolanaLiveExecutor(app, tid, wallet_id=row.get("wallet_id")), bal

    if len(signing_holders) > 1:
        # Prefer the recorded entry binding if it is among the holders; otherwise
        # refuse to guess which wallet should bear the position's cost basis.
        if bound:
            for row, bal in signing_holders:
                if str(row.get("wallet_id")) == str(bound.get("wallet_id")):
                    return _exec.SolanaLiveExecutor(app, tid, wallet_id=row.get("wallet_id")), bal
        _quarantine(app, position, "matching token is spread across or present in multiple signing wallets")

    if holders:
        _quarantine(app, position, "matching token exists only in a disabled/public-only Solana wallet")

    _quarantine(app, position, "none of the user's registered Solana wallets holds the recorded token mint")


def _executor_init(self, app, telegram_id, wallet_id=None):
    self.app = app
    self.telegram_id = str(telegram_id)
    self.store = SolanaWalletStore(app.csv_dir, app.data_dir)
    self.meta = self.store.get_meta(self.telegram_id, wallet_id)
    self.wallet_id = str(self.meta.get("wallet_id") or "")
    if not self.store.has_private_key(self.telegram_id, self.wallet_id):
        raise _exec.SolanaLiveError("Selected Solana wallet has no encrypted signing key")
    self.address = str(self.meta.get("address") or "")
    self.keypair = self.store.keypair_bytes(self.telegram_id, self.wallet_id)


def _swap_with_wallet_identity(self, input_mint, output_mint, amount_raw):
    try:
        result = _PREV_SWAP(self, input_mint, output_mint, amount_raw)
    except _exec.SolanaLivePostExecutionError as exc:
        exc.result["wallet_id"] = str(getattr(self, "wallet_id", ""))
        exc.result["wallet_address"] = str(getattr(self, "address", ""))
        raise
    result = dict(result or {})
    result["wallet_id"] = str(getattr(self, "wallet_id", ""))
    result["wallet_address"] = str(getattr(self, "address", ""))
    return result


def _insert_with_wallet_binding(app, tid, rank, event, trade, allocation, cfg):
    pid, out_raw, entry_cost = _PREV_INSERT(app, tid, rank, event, trade, allocation, cfg)
    wallet_id = str((trade or {}).get("wallet_id") or "")
    address = str((trade or {}).get("wallet_address") or "")
    if wallet_id and address:
        _bind(app, pid, tid, wallet_id, address, "ENTRY")
    return pid, out_raw, entry_cost


def _close_bound_live(app, tid, position, fraction: Decimal, reason: str):
    executor, actual = _resolve_executor(app, tid, position)
    old_raw = max(1, _sol._int(position.get("token_amount_raw"), 0))
    f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    planned = max(1, int(Decimal(old_raw) * f))
    sell_raw = min(planned, int(actual))
    if sell_raw <= 0:
        _quarantine(app, position, "resolved wallet has no sellable matching token")

    trade = executor.sell(position["mint"], sell_raw)
    out_lamports = int(trade.get("totalOutputAmount") or trade.get("outputAmountResult") or 0)
    delta_raw = trade.get("wallet_delta_lamports")
    try:
        delta = int(delta_raw) if delta_raw is not None else 0
    except Exception:
        delta = 0
    cfg = _sol.settings(app)
    if delta > 0:
        proceeds = Decimal(delta) / Decimal(1_000_000_000)
    else:
        proceeds = Decimal(out_lamports) / Decimal(1_000_000_000) - _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002")

    old_cost = _sol._dec(position.get("entry_cost_sol"), 0)
    cost_fraction = old_cost * Decimal(sell_raw) / Decimal(old_raw)
    net = proceeds - cost_fraction
    remaining = max(0, old_raw - sell_raw)
    remaining_cost = max(Decimal(0), old_cost - cost_fraction)
    closed = remaining <= max(1, int(old_raw * .001)) or f >= Decimal("0.999")
    realised = _sol._dec(position.get("realised_net_sol"), 0) + net
    now = int(time.time())
    sig = str(trade.get("signature") or "")
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,realised_net_sol=?,exit_signature=?,exit_reason=?,closed_at=?,
                                    status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
            (str(0 if closed else remaining), str(0 if closed else remaining_cost), str(realised), sig, reason,
             now if closed else None, "CLOSED" if closed else "OPEN", 0 if closed else int(position.get("leader_exit_pending") or 0), now, position["position_id"]),
        )
        conn.commit()
    _live._notify(
        app,
        tid,
        f"✅ <b>Solana LIVE SELL confirmed</b>\n"
        f"Reason: <code>{reason}</code>\n"
        f"Wallet: <code>{executor.address[:8]}…{executor.address[-6:]}</code>\n"
        f"Received wallet delta: <b>{proceeds:.9f} SOL</b>\n"
        f"Net on sold portion: <b>{net:+.9f} SOL</b>\n"
        f"TX: <code>{sig}</code>",
    )
    return {"closed": closed, "net_sol": net, "signature": sig, "reason": reason, "trade": trade}


def install():
    if getattr(_live, "_position_wallet_binding_patch_installed", False):
        return
    _exec.SolanaLiveExecutor.__init__ = _executor_init
    _exec.SolanaLiveExecutor.swap = _swap_with_wallet_identity
    _live.SolanaLiveExecutor = _exec.SolanaLiveExecutor
    _live._insert_live_position = _insert_with_wallet_binding
    _live._close_live = _close_bound_live
    _live._position_wallet_binding_patch_installed = True


install()
