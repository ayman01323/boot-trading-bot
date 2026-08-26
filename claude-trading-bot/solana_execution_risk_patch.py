"""Wires Claude-specific hard limits into the actual Solana LIVE execution path.

The wrapper sits immediately in front of SolanaLiveExecutor.buy/sell. New
entries must pass identity, signer, chain, operating-state (ARMED, not
HALTED_DRAWDOWN), position/exposure/count, and drawdown checks. Exits
deliberately keep only identity/signing checks so a risk stop can never trap
capital in an existing position.

Consolidated per direct owner instruction (2026-08-26): this module used to
own its own ad-hoc drawdown-halt persistence and its own Telegram command
handler (/sibot1riskresume, /sibot1riskstatus -- confusingly named after the
unrelated production SiBot). Both responsibilities have moved out:
  - persistent state (OFF/ARMED/STOPPING + the HALTED_DRAWDOWN latch) now
    lives in claude_state.py, the one authoritative state machine;
  - the Telegram command surface now lives in telegram_control_patch.py, the
    one authoritative command router (/claude_status, /claude_arm_live,
    /claude_disarm, /claude_stop, /claude_restart_request,
    /claude_restart_confirm).
This module keeps exactly what belongs at the execution boundary: reading
this instance's own isolated position history to compute exposure/drawdown,
and refusing to execute when any control says no.
"""

from __future__ import annotations

import json
import os
import urllib.request
from contextlib import closing
from decimal import Decimal

from learnerbot import solana_live_executor as _executor
from learnerbot import solana_sibot as _sol

import claude_state
import risk_engine_guard
import signing_interface

_SOL_MINT = "So11111111111111111111111111111111111111112"
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_original_buy = _executor.SolanaLiveExecutor.buy
_original_sell = _executor.SolanaLiveExecutor.sell


class ExecutionGuardError(RuntimeError):
    """Raised when a guarded call is refused. Never bypassable from outside this module."""


def _owner_id() -> str:
    return os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()


def sol_usd_price() -> Decimal:
    """Live SOL/USD price via Jupiter's public quote API; failure is fail-closed."""
    url = (
        "https://lite-api.jup.ag/swap/v1/quote?"
        f"inputMint={_SOL_MINT}&outputMint={_USDC_MINT}&amount=1000000000&slippageBps=50"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    out_amount = Decimal(str(data["outAmount"]))
    return out_amount / Decimal(1_000_000)


def check_identity_and_signer(app, telegram_id) -> None:
    owner_id = _owner_id()
    if not owner_id:
        raise ExecutionGuardError("CLAUDE_BOT_WALLET_OWNER_ID is not set")
    if str(telegram_id) != owner_id:
        raise ExecutionGuardError(
            f"Identity {telegram_id!r} does not match CLAUDE_BOT_WALLET_OWNER_ID={owner_id!r} "
            f"-- refusing to sign/broadcast for an identity this bot did not explicitly authorise"
        )
    status = signing_interface.get_signer_status(app)
    if not status.ready:
        raise ExecutionGuardError(f"Refusing to sign/broadcast: {status.reason}")


def check_chain_authorised(chain: str) -> None:
    authorised = {c.strip().lower() for c in os.environ.get("AUTHORISED_CHAINS", "").split(",") if c.strip()}
    if chain.lower() not in authorised:
        raise ExecutionGuardError(
            f"Chain {chain!r} is not in AUTHORISED_CHAINS={sorted(authorised) or '(none)'} "
            f"-- no chain is authorised by default, the operator must set this explicitly"
        )


def restart_preconditions(app) -> None:
    """Rechecked before HALTED_DRAWDOWN may clear -- risk config valid,
    signer ready, chain still authorised. Raises on any failure."""
    risk_engine_guard.RiskLimits.load()
    check_identity_and_signer(app, _owner_id())
    check_chain_authorised("solana")


def _current_live_exposure_sol(app, telegram_id) -> Decimal:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(entry_cost_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _current_live_open_count(app, telegram_id) -> int:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return int(row["n"])


def _peak_to_current_drawdown_sol(app, telegram_id, *, since_epoch: int = 0) -> Decimal:
    """Current running-peak realized-P&L drawdown since the active baseline."""
    with closing(_sol.connect(app)) as conn:
        if int(since_epoch or 0) > 0:
            rows = conn.execute(
                "SELECT realised_net_sol FROM positions "
                "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' AND closed_at >= ? "
                "ORDER BY closed_at ASC",
                (str(telegram_id), int(since_epoch)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT realised_net_sol FROM positions "
                "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE' ORDER BY closed_at ASC",
                (str(telegram_id),),
            ).fetchall()
    cumulative = Decimal(0)
    peak = Decimal(0)
    for row in rows:
        cumulative += Decimal(str(row["realised_net_sol"] or 0))
        peak = max(peak, cumulative)
    return peak - cumulative


def position_snapshot(app, telegram_id, *, baseline_epoch: int) -> dict:
    """One place that reads this instance's own position history and turns
    it into the numbers every caller needs (execution guard, /claude_status,
    tests) -- never re-derived independently elsewhere."""
    price = sol_usd_price()
    exposure_usd = _current_live_exposure_sol(app, telegram_id) * price
    open_positions = _current_live_open_count(app, telegram_id)
    drawdown_usd = _peak_to_current_drawdown_sol(app, telegram_id, since_epoch=baseline_epoch) * price
    return {
        "price_usd": price,
        "exposure_usd": exposure_usd,
        "open_positions": open_positions,
        "drawdown_usd": drawdown_usd,
    }


def _send_owner_drawdown_alert(app, *, drawdown_pct: Decimal, drawdown_usd: Decimal, open_positions: int) -> None:
    from learnerbot import telegram as _telegram
    import time

    owner_id = _owner_id()
    token = str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or not owner_id:
        return
    limits = risk_engine_guard.RiskLimits.load()
    text = (
        "🛑 <b>CLAUDE BOT HALTED — 20% DRAWDOWN LIMIT REACHED</b>\n"
        f"Current drawdown: <b>{drawdown_pct:.2f}%</b> (${drawdown_usd:.2f})\n"
        f"Capital basis: ${limits.capital_basis_usd:.2f}\n"
        f"Open positions: {open_positions}\n"
        f"Timestamp: {int(time.time())}\n\n"
        "New entries stopped. Risk-reducing exits remain subject to normal safety controls.\n"
        "Trading will NOT restart automatically. Restart requires explicit authorisation "
        "from the wallet owner: <code>/claude_restart_request</code> then "
        "<code>/claude_restart_confirm CONFIRM</code>."
    )
    try:
        _telegram.send_message(token, owner_id, text, parse_mode="HTML", protect_content=True)
    except Exception as exc:
        print("[claude-drawdown-alert]", type(exc).__name__, str(exc)[:240])


def _guarded_buy(self, output_mint: str, amount_sol, reserve_sol) -> dict:
    check_identity_and_signer(self.app, self.telegram_id)
    check_chain_authorised("solana")

    state = claude_state.load_state(self.app)
    if state.get("halted_drawdown"):
        raise ExecutionGuardError(
            "Drawdown circuit breaker is latched (HALTED_DRAWDOWN). New entries stay blocked "
            "until the wallet owner clears it with /claude_restart_request then "
            "/claude_restart_confirm CONFIRM. Exits remain available."
        )
    if not claude_state.is_armed(state):
        raise ExecutionGuardError(
            f"Not ARMED (operating_state={state.get('operating_state')}). "
            f"The wallet owner must send /claude_arm_live CONFIRM from Telegram first."
        )

    limits = risk_engine_guard.RiskLimits.load()
    snapshot = position_snapshot(self.app, self.telegram_id, baseline_epoch=state.get("baseline_epoch") or 0)
    proposed_usd = Decimal(str(amount_sol)) * snapshot["price_usd"]

    limits.check_new_position(
        proposed_usd=proposed_usd,
        current_exposure_usd=snapshot["exposure_usd"],
        open_positions=snapshot["open_positions"],
    )

    try:
        limits.check_drawdown(snapshot["drawdown_usd"])
    except risk_engine_guard.DrawdownLimitBreached as breach:
        first = claude_state.latch_drawdown(
            self.app, drawdown_pct=breach.drawdown_pct, drawdown_usd=breach.drawdown_usd
        )
        if first:
            _send_owner_drawdown_alert(
                self.app,
                drawdown_pct=breach.drawdown_pct,
                drawdown_usd=breach.drawdown_usd,
                open_positions=snapshot["open_positions"],
            )
        raise ExecutionGuardError(str(breach)) from breach

    return _original_buy(self, output_mint, amount_sol, reserve_sol)


def _guarded_sell(self, input_mint: str, amount_raw: int) -> dict:
    # Exits remain possible during a drawdown halt or while not armed: reducing
    # risk must never be blocked by an entry-only circuit breaker.
    check_identity_and_signer(self.app, self.telegram_id)
    return _original_sell(self, input_mint, amount_raw)


def install() -> None:
    if not getattr(_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        _executor.SolanaLiveExecutor.buy = _guarded_buy
        _executor.SolanaLiveExecutor.sell = _guarded_sell
        _executor.SolanaLiveExecutor._claude_risk_guard_installed = True
