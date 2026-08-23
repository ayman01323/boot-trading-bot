from __future__ import annotations

import time
from contextlib import closing

from . import sibot as _sibot

# Wrap the final queue assembled by the Alchemy history/retry/trace patches.
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet

# Conservative default: at most one orphaned legacy-error wallet per chain every
# 15 minutes. The old proposal checked each row's fetched_at, which did not
# actually enforce a per-chain cooldown and could sweep a new old row every
# 12-second worker pass.
#
# This must be checked BEFORE the ranked/progress queue, not only as a fallback
# when that queue is empty. On a chain with an actively-refreshing top-N
# candidate window (new wallets constantly entering top_history_candidate_wallets
# as scores update, or a context-progress/trace-progress layer above this one
# re-prioritising an in-progress wallet), the ranked queue can go years without
# ever returning None -- confirmed live: BSC kept advancing its "newest fetch"
# timestamp every pass for hours with its error count completely unchanged,
# because the same ~40-wallet ranked window kept finding *something* to retry
# every single pass, so the fallback-only-when-idle sweep never got a single
# turn. The 15-minute per-chain cooldown below is what actually bounds how
# often this can run, not the queue's business -- so checking it first costs
# nothing extra on the ~74 out of ~75 passes (at the default 12s worker
# interval) where the cooldown says not yet, and it guarantees the sweep
# genuinely fires once per cooldown window instead of being crowded out
# indefinitely by a queue that may never go idle in practice.
_DEFAULT_SWEEP_SECONDS = 15 * 60
_MIN_SWEEP_SECONDS = 5 * 60
_MAX_SWEEP_SECONDS = 60 * 60
_LEGACY_ERROR_FRAGMENT = "ETHERSCAN_API_KEY is not configured"
_STATE_PREFIX = "legacy_etherscan_sweep_last"


def _sweep_seconds(app, chain) -> int:
    cfg = _sibot.platform_settings(app, int(chain.chain_id))
    value = _sibot._int(cfg.get("legacy_etherscan_sweep_seconds"), _DEFAULT_SWEEP_SECONDS)
    return max(_MIN_SWEEP_SECONDS, min(_MAX_SWEEP_SECONDS, value))


def _next_legacy_error_wallet(app, chain, now_epoch: int | None = None) -> str | None:
    """Return one otherwise-orphaned pre-Alchemy error row under a durable chain cooldown.

    This is deliberately independent of the top history_candidate_wallets window.
    The per-chain cooldown -- not queue idleness -- is what bounds how often this
    can preempt a ranked candidate: it returns None on every call within the
    cooldown window (a cheap single state read, no wallet query), so checking it
    first is effectively free except on the rare pass where it's actually due.
    The per-chain timestamp is persisted in the SiBot state table before the
    wallet is handed to the refresher, so a restart or a refresh exception cannot
    turn the sweep into a tight retry loop.
    """
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    key = f"{_STATE_PREFIX}:{int(chain.chain_id)}"
    cooldown = _sweep_seconds(app, chain)

    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        last = _sibot._int(_sibot._state(conn, key, 0), 0)
        if last > 0 and now - last < cooldown:
            return None
        row = conn.execute(
            """SELECT wallet FROM wallet_history_status
               WHERE chain_id=? AND error LIKE ?
               ORDER BY fetched_at ASC, wallet ASC LIMIT 1""",
            (int(chain.chain_id), f"%{_LEGACY_ERROR_FRAGMENT}%"),
        ).fetchone()
        if not row:
            return None
        wallet = str(row["wallet"] or "").lower().strip()
        if not wallet:
            return None
        _sibot._set_state(conn, key, now)
        return wallet


def _next_history_wallet(app, chain):
    # Check the cooldown-gated sweep first. On every pass except the rare one
    # where a chain's cooldown has actually elapsed, this returns None
    # immediately (one cheap state read) and falls through to the ranked
    # queue exactly as before -- but on the passes where it IS due, it must
    # win, or a ranked queue that never goes idle starves it forever.
    legacy = _next_legacy_error_wallet(app, chain)
    if legacy:
        return legacy
    return _PREV_NEXT_HISTORY_WALLET(app, chain)


def install() -> None:
    if getattr(_sibot, "_legacy_error_sweep_patch_installed", False):
        return
    _sibot._next_history_wallet = _next_history_wallet
    _sibot._legacy_error_sweep_patch_installed = True
    print(
        "[sibot-legacy-error-sweep] checked_first=true per_chain_cooldown=15m "
        "legacy_etherscan_only=true"
    )


install()
