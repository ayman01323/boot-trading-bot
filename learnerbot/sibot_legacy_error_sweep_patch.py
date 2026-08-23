from __future__ import annotations

import time
from contextlib import closing

from . import sibot as _sibot

# Wrap the final queue assembled by the Alchemy history/retry/trace patches.
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet

# Conservative default: at most one otherwise-orphaned legacy Etherscan wallet per
# chain every 15 minutes, and only when the normal ranked/progress queue has nothing
# to do. Ranked candidate rows are migrated faster by sibot_alchemy_retry_queue_patch.
_DEFAULT_SWEEP_SECONDS = 15 * 60
_MIN_SWEEP_SECONDS = 5 * 60
_MAX_SWEEP_SECONDS = 60 * 60
_STATE_PREFIX = "legacy_etherscan_sweep_last"


def _sweep_seconds(app, chain) -> int:
    cfg = _sibot.platform_settings(app, int(chain.chain_id))
    value = _sibot._int(cfg.get("legacy_etherscan_sweep_seconds"), _DEFAULT_SWEEP_SECONDS)
    return max(_MIN_SWEEP_SECONDS, min(_MAX_SWEEP_SECONDS, value))


def _next_legacy_error_wallet(app, chain, now_epoch: int | None = None) -> str | None:
    """Return one otherwise-orphaned pre-Alchemy Etherscan error row.

    This is deliberately independent of the top history_candidate_wallets window.
    It never preempts ranked candidates because callers invoke it only after the
    fully patched primary queue returns None. All Etherscan-origin errors are
    migration backlog once the Alchemy provider stack is installed, including
    missing/invalid keys and chain-plan NOTOK responses.
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
               WHERE chain_id=? AND lower(COALESCE(error,'')) LIKE '%etherscan%'
               ORDER BY fetched_at ASC, wallet ASC LIMIT 1""",
            (int(chain.chain_id),),
        ).fetchone()
        if not row:
            return None
        wallet = str(row["wallet"] or "").lower().strip()
        if not wallet:
            return None
        _sibot._set_state(conn, key, now)
        return wallet


def _next_history_wallet(app, chain):
    primary = _PREV_NEXT_HISTORY_WALLET(app, chain)
    if primary:
        return primary
    return _next_legacy_error_wallet(app, chain)


def install() -> None:
    if getattr(_sibot, "_legacy_error_sweep_patch_installed", False):
        return
    _sibot._next_history_wallet = _next_history_wallet
    _sibot._legacy_error_sweep_patch_installed = True
    print(
        "[sibot-legacy-error-sweep] fallback_only=true per_chain_cooldown=15m "
        "all_etherscan_origin_errors=true"
    )


install()
