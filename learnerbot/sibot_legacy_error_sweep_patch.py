from __future__ import annotations

import time
from contextlib import closing

from . import sibot as _sibot

# Wrap the final queue assembled by the Alchemy history/retry/trace patches.
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet

# Conservative default: at most one otherwise-orphaned legacy Etherscan wallet per
# chain every 15 minutes. The durable cooldown, rather than primary-queue idleness,
# bounds this migration work. This matters because a ranked/progress queue can stay
# permanently busy; if legacy migration is fallback-only, old rows outside the
# candidate window can be starved forever. Ranked candidate Etherscan rows are still
# migrated faster by sibot_alchemy_retry_queue_patch.
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
    All Etherscan-origin errors are migration backlog once the Alchemy provider
    stack is installed, including missing/invalid keys and chain-plan NOTOK
    responses. The per-chain timestamp is persisted before the wallet is handed to
    the refresher, so restarts or refresh exceptions cannot create a tight loop.
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
    # Check the cooldown-gated legacy sweep before the ranked/progress queue.
    # On almost every worker pass the durable cooldown returns None immediately
    # and the primary queue proceeds unchanged. When the sweep is due, allowing
    # one legacy row to win prevents indefinite starvation behind a queue that
    # may never become idle.
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
        "all_etherscan_origin_errors=true"
    )


install()
