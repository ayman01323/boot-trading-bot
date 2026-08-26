"""Wires the Claude-specific hard limits into the actual Solana LIVE execution
path -- both entry (buy) and exit (sell), per review.

Wraps SolanaLiveExecutor.buy/sell -- the real signing/broadcast entry points
in the reused, unmodified learnerbot code -- following the same monkey-patch
convention this codebase already uses for evm_pool_rug_gate.py /
solana_pool_risk_gate.py, rather than editing solana_live_executor.py
directly. Must be imported (via bootstrap_run.py) before learnerbot.__main__
builds its own patch chain, same requirement as identity_patch.py.

What each guarded call actually enforces, in order, before falling through
to the real (unmodified) executor method:
  1. Runtime identity match: self.telegram_id (the identity THIS executor
     instance was actually constructed with) must equal
     CLAUDE_BOT_WALLET_OWNER_ID. Closes the gap review found: a mismatched
     runtime identity that happens to have its own signing key could
     otherwise reach the reused executor even though signing_interface's
     SIGNER_READY status describes a different wallet entirely.
  2. SIGNER_READY: re-checked here, not just reported at startup/preflight.
  3. AUTHORISED_CHAINS: must contain "solana" (case-insensitive). Defaults
     to nothing authorised if unset -- fail closed, no chain is assumed
     authorised by this code. (buy only -- see _guarded_sell for why exits
     don't gate on this.)
  4. risk_engine_guard.check_new_position() (buy only): position size /
     total exposure / open-position-count caps, priced via a live Jupiter
     quote.
  5. risk_engine_guard.check_daily_loss_and_drawdown() (buy only): realized
     P&L for today and running drawdown, computed from this instance's own
     closed-position history.

Exits (sell) go through checks 1-2 only, not 3-4-5: closing an existing
position reduces risk rather than adding it, and revoking chain
authorisation or hitting a risk cap mid-position should not trap capital in
a position this bot can no longer exit. Identity/signing checks still apply
to sells because they are still a real signing/broadcast event.

EVM is not wired here: all working EVM RPC endpoints found so far (Ethereum
1/2, BSC 2/3 per diagnostics/claude-google-runtime-check.txt) still have no
equivalent execution guard, and AUTHORISED_CHAINS defaults to no chains
authorised regardless -- so EVM cannot execute through this bot yet even
though some of its RPCs are reachable. Add an equivalent wrapper for
LiveTrader.buy/sell before ever authorising an EVM chain.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from contextlib import closing
from decimal import Decimal

from learnerbot import solana_live_executor as _executor
from learnerbot import solana_sibot as _sol

import risk_engine_guard
import signing_interface

_SOL_MINT = "So11111111111111111111111111111111111111112"
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_SECONDS_PER_DAY = 86400

_original_buy = _executor.SolanaLiveExecutor.buy
_original_sell = _executor.SolanaLiveExecutor.sell


class ExecutionGuardError(RuntimeError):
    """Raised when a guarded call is refused. Never bypassable from outside this module."""


def _sol_usd_price() -> Decimal:
    """Live SOL/USD price via Jupiter's public quote API.

    Raises on any failure -- callers must fail closed (reject the trade)
    rather than guess a price, since an unknown price makes every USD-based
    check in this module meaningless rather than merely stale.
    """
    url = (
        "https://lite-api.jup.ag/swap/v1/quote?"
        f"inputMint={_SOL_MINT}&outputMint={_USDC_MINT}&amount=1000000000&slippageBps=50"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    out_amount = Decimal(str(data["outAmount"]))  # USDC out for 1 SOL in
    return out_amount / Decimal(1_000_000)  # USDC has 6 decimals


def _check_identity_and_signer(executor) -> None:
    owner_id = os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()
    if not owner_id:
        raise ExecutionGuardError("CLAUDE_BOT_WALLET_OWNER_ID is not set")
    if str(executor.telegram_id) != owner_id:
        raise ExecutionGuardError(
            f"Executor identity {executor.telegram_id!r} does not match "
            f"CLAUDE_BOT_WALLET_OWNER_ID={owner_id!r} -- refusing to sign/broadcast "
            f"for an identity this bot did not explicitly authorise"
        )
    status = signing_interface.get_signer_status(executor.app)
    if not status.ready:
        raise ExecutionGuardError(f"Refusing to sign/broadcast: {status.reason}")


def _check_chain_authorised(chain: str) -> None:
    authorised = {c.strip().lower() for c in os.environ.get("AUTHORISED_CHAINS", "").split(",") if c.strip()}
    if chain.lower() not in authorised:
        raise ExecutionGuardError(
            f"Chain {chain!r} is not in AUTHORISED_CHAINS={sorted(authorised) or '(none)'} "
            f"-- no chain is authorised by default, the operator must set this explicitly"
        )


def _current_live_exposure_sol(app, telegram_id: str) -> Decimal:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(entry_cost_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _current_live_open_count(app, telegram_id: str) -> int:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return int(row["n"])


def _realized_pnl_sol_today(app, telegram_id: str) -> Decimal:
    day_start = int(time.time() // _SECONDS_PER_DAY) * _SECONDS_PER_DAY
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(realised_net_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' AND closed_at >= ?",
            (str(telegram_id), day_start),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _peak_to_current_drawdown_sol(app, telegram_id: str) -> Decimal:
    """Running peak of cumulative realized P&L minus current cumulative P&L.

    Approximation, documented as such: this is a simple running-peak
    drawdown over this instance's own closed-LIVE-position history, not a
    mark-to-market intraday drawdown (open positions' unrealized P&L is not
    included). Correct and conservative for what it measures; not a
    substitute for monitoring unrealized P&L separately.
    """
    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            "SELECT realised_net_sol FROM positions "
            "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' ORDER BY closed_at ASC",
            (str(telegram_id),),
        ).fetchall()
    cumulative = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    for row in rows:
        cumulative += Decimal(str(row["realised_net_sol"] or 0))
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return max_drawdown


def _guarded_buy(self, output_mint: str, amount_sol, reserve_sol) -> dict:
    _check_identity_and_signer(self)
    _check_chain_authorised("solana")

    limits = risk_engine_guard.RiskLimits.load()
    price = _sol_usd_price()

    proposed_usd = float(Decimal(str(amount_sol)) * price)
    current_exposure_usd = float(_current_live_exposure_sol(self.app, self.telegram_id) * price)
    open_positions = _current_live_open_count(self.app, self.telegram_id)
    limits.check_new_position(
        proposed_usd=proposed_usd,
        current_exposure_usd=current_exposure_usd,
        open_positions=open_positions,
    )

    realized_pnl_usd_today = float(_realized_pnl_sol_today(self.app, self.telegram_id) * price)
    drawdown_usd = float(_peak_to_current_drawdown_sol(self.app, self.telegram_id) * price)
    limits.check_daily_loss_and_drawdown(
        realized_pnl_usd_today=realized_pnl_usd_today,
        peak_to_current_drawdown_usd=drawdown_usd,
    )

    return _original_buy(self, output_mint, amount_sol, reserve_sol)


def _guarded_sell(self, input_mint: str, amount_raw: int) -> dict:
    # Identity/signing checks only -- see module docstring for why exits do
    # not gate on AUTHORISED_CHAINS or the risk-size/daily-loss/drawdown
    # checks that apply to new entries.
    _check_identity_and_signer(self)
    return _original_sell(self, input_mint, amount_raw)


def install() -> None:
    if getattr(_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        return
    _executor.SolanaLiveExecutor.buy = _guarded_buy
    _executor.SolanaLiveExecutor.sell = _guarded_sell
    _executor.SolanaLiveExecutor._claude_risk_guard_installed = True
