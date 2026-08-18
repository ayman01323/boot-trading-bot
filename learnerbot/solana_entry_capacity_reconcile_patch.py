from __future__ import annotations

import time
from contextlib import closing

from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol
from .solana_wallet_store import SolanaWalletStore


def _verified_open_live_count(app, tid):
    """Count only verified LIVE positions, safely quarantining proven-empty stale rows.

    Capacity is freed only when every registered wallet balance query succeeds and
    every result is zero. Any RPC uncertainty fails closed: the DB row remains OPEN
    and continues to consume capacity rather than risking an extra real position.
    """
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE' ORDER BY entry_ts",
            (str(tid),),
        ).fetchall()]

    if not rows:
        return 0

    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        wallets = list(store.list_wallets(tid, enabled_only=False))
    except Exception:
        return len(rows)

    addresses = []
    seen = set()
    for wallet in wallets:
        address = str(wallet.get("address") or "").strip()
        if address and address not in seen:
            seen.add(address)
            addresses.append(address)

    # No registered address means the position cannot be proved empty safely.
    if not addresses:
        return len(rows)

    verified_open = 0
    for position in rows:
        mint = str(position.get("mint") or "").strip()
        if not mint:
            verified_open += 1
            continue

        all_checked = True
        any_balance = False
        for address in addresses:
            try:
                balance = int(_binding._token_balance_for_address(app, address, mint))
            except Exception:
                all_checked = False
                break
            if balance > 0:
                any_balance = True
                break

        if any_balance or not all_checked:
            verified_open += 1
            continue

        # Every registered wallet was successfully checked and none holds the mint:
        # this is a proven stale DB position. Quarantine it so future entries are not
        # permanently blocked, without pretending a SELL occurred.
        now = int(time.time())
        reason = "capacity reconciliation: all registered Solana wallets verified zero balance for recorded mint"
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            conn.execute(
                """UPDATE positions
                   SET status='RECONCILE_REQUIRED',exit_reason=?,leader_exit_pending=0,updated_at=?
                   WHERE position_id=? AND status='OPEN' AND mode='LIVE'""",
                (reason, now, str(position.get("position_id") or "")),
            )
            conn.commit()
        print(
            "[solana-capacity-reconcile] tid=%s position=%s action=RECONCILE_REQUIRED reason=verified_zero_balance"
            % (str(tid), str(position.get("position_id") or "")[:16])
        )

    return verified_open


def install():
    _live._open_live_count = _verified_open_live_count
    print("[solana-capacity-reconcile] verified_zero_only=true rpc_uncertainty=fails_closed")


install()
