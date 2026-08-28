from __future__ import annotations

from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_sibot as _sol
from .user_registry import user_setting


def effective_minimum_trade(app, tid, cfg=None) -> Decimal:
    """Return the effective per-user minimum economic LIVE trade.

    Telegram/user overrides are restriction-only: they may raise the minimum but
    can never weaken the platform-configured safety floor.
    """
    cfg = dict(cfg or _sol.settings(app))
    platform_minimum = max(
        Decimal("0.0001"),
        _sol._dec(cfg.get("live_min_economic_trade_sol"), "0.0005"),
    )
    raw = user_setting(
        app.csv_dir,
        tid,
        _sol.SOLANA_CHAIN_ID,
        "solana_live_min_economic_trade_sol",
        None,
    )
    if raw is None:
        return platform_minimum
    return max(platform_minimum, _sol._dec(raw, platform_minimum))


def _economic_entry_gate_reconciled(app, tid, allocation, cfg):
    """Keep the hard minimum trade-size gate without using unreconciled BUY cash delta.

    A Solana BUY wallet delta can include temporary/refundable token-account or WSOL
    funding. Treating ``-wallet_delta - swap_input`` as irreversible execution
    overhead before the corresponding accounts are closed can grossly overstate
    cost and block every later LIVE entry. Actual wallet deltas remain persisted
    for audit/P&L; they are simply not used as a pre-trade global blocker until a
    round trip has been reconciled.

    The per-user Telegram setting can only raise this minimum. It can never reduce
    the platform safety floor.
    """
    minimum = effective_minimum_trade(app, tid, cfg)
    amount = Decimal(str(allocation))
    if amount < minimum:
        return False, f"LIVE allocation {allocation} SOL is below economic minimum {minimum} SOL"
    return True, "ok"


def install():
    _live._economic_entry_gate = _economic_entry_gate_reconciled
    print(
        "[solana-overhead-gate] "
        "unreconciled_entry_wallet_delta_block=false hard_minimum=true user_minimum=restriction_only"
    )


install()
