"""Wires risk_engine_guard's hard limits into the actual Solana LIVE execution
path. Closes a real gap GPT's review caught: risk_engine_guard.py existed and
was validated at startup, but nothing ever called check_new_position() before
a real trade -- the README's "sits in front of execution" claim was stronger
than the implementation.

Wraps SolanaLiveExecutor.buy -- the real signing/broadcast entry point in the
reused, unmodified learnerbot code -- following the same monkey-patch
convention this codebase already uses for evm_pool_rug_gate.py /
solana_pool_risk_gate.py, rather than editing solana_live_executor.py
directly. Must be imported (via bootstrap_run.py) before learnerbot.__main__
builds its own patch chain, same requirement as identity_patch.py.

EVM is not wired here: all 5 EVM chains currently FAIL connectivity per
diagnostics/claude-google-runtime-check.txt, so there is nothing live to
guard yet. Add an equivalent wrapper for LiveTrader.buy once EVM is healthy.
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import closing
from decimal import Decimal

from learnerbot import solana_live_executor as _executor
from learnerbot import solana_sibot as _sol

import risk_engine_guard

_SOL_MINT = "So11111111111111111111111111111111111111112"
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_original_buy = _executor.SolanaLiveExecutor.buy


def _sol_usd_price() -> Decimal:
    """Live SOL/USD price via Jupiter's public quote API.

    Raises on any failure -- callers must fail closed (reject the trade)
    rather than guess a price, since an unknown price makes the USD risk
    check meaningless rather than merely stale.
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


def _guarded_buy(self, output_mint: str, amount_sol, reserve_sol) -> dict:
    # RiskLimits.load() re-reads env each call -- cheap, and guarantees a live
    # config edit (or a config that went missing) is honoured immediately
    # rather than only at process startup.
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
    return _original_buy(self, output_mint, amount_sol, reserve_sol)


def install() -> None:
    if getattr(_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        return
    _executor.SolanaLiveExecutor.buy = _guarded_buy
    _executor.SolanaLiveExecutor._claude_risk_guard_installed = True
