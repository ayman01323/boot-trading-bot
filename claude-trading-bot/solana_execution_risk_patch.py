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


def armed_health_check(app, telegram_id) -> str | None:
    """THE one authoritative "is it still safe to be ARMED" check (review,
    2026-08-26): used before every entry, by /claude_arm_live and
    /claude_restart_confirm's precondition recheck, and by
    claude_monitor.py's periodic loop while ARMED -- never re-derived
    independently at any of those call sites. Returns None if every
    critical precondition holds, else a human-readable reason. Never raises."""
    try:
        risk_engine_guard.RiskLimits.load()
    except risk_engine_guard.RiskGuardConfigError as exc:
        return f"risk config invalid: {exc}"
    try:
        check_identity_and_signer(app, telegram_id)
    except ExecutionGuardError as exc:
        return f"signer/identity: {exc}"
    try:
        check_chain_authorised("solana")
    except ExecutionGuardError as exc:
        return f"chain: {exc}"
    try:
        general = app.general()
        engine_on = str(general.get("engine_enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
        if not engine_on:
            return "kill-switch active (this instance's own engine_enabled=false)"
    except Exception as exc:  # noqa: BLE001
        return f"kill-switch state unreadable: {type(exc).__name__}: {exc}"
    if _executor.SolanaLiveExecutor.buy is not _guarded_buy or _executor.SolanaLiveExecutor.sell is not _guarded_sell:
        return "Claude execution guard is no longer installed on SolanaLiveExecutor"
    return None


def restart_preconditions(app) -> None:
    """Rechecked before HALTED_DRAWDOWN may clear -- same authoritative
    check ARMED itself depends on. Raises on any failure."""
    reason = armed_health_check(app, _owner_id())
    if reason:
        raise ExecutionGuardError(f"Restart preconditions not met: {reason}")


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


def _current_unrealised_pnl_sol(app, telegram_id) -> Decimal:
    """Sum of unrealised_net_sol across this instance's own OPEN LIVE
    positions -- a mark-to-market figure learnerbot's own scanner loop
    already maintains per position (see learnerbot/solana_sibot.py's
    periodic UPDATE), reused here rather than re-implemented. This is
    inherently a "right now" value, so pricing it at the current SOL/USD
    rate (once, at read time) introduces no historical-repricing artifact."""
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(unrealised_net_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _cumulative_realized_sol(app, telegram_id) -> Decimal:
    """Sum of realised_net_sol across ALL this instance's own CLOSED LIVE
    positions, ever. Used ONLY as a before/after snapshot around a single
    sell call (see _guarded_sell) to isolate that one trade's own
    contribution -- never used directly as a drawdown figure itself, which
    is exactly the "reprice history at today's price" mistake review
    flagged. The isolated delta is priced once, immediately, at the moment
    it is realised (see record_realized_pnl's call site), then permanently
    fixed in claude_state's running USD total."""
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(realised_net_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def position_snapshot(app, telegram_id) -> dict:
    """Exposure/open-count numbers every caller needs (execution guard,
    /claude_status, tests) -- never re-derived independently elsewhere.
    Equity/drawdown is a SEPARATE concern, see compute_current_equity_usd()."""
    price = sol_usd_price()
    exposure_usd = _current_live_exposure_sol(app, telegram_id) * price
    open_positions = _current_live_open_count(app, telegram_id)
    return {
        "price_usd": price,
        "exposure_usd": exposure_usd,
        "open_positions": open_positions,
    }


def compute_current_equity_usd(app, telegram_id, *, capital_basis_usd: Decimal) -> dict:
    """THE one authoritative current-equity function (review, 2026-08-26):
    capital basis + a running realised-P&L-in-USD total (each trade priced
    once, at its own close time -- see claude_state.record_realized_pnl)
    + today's mark-to-market of open positions (inherently a "now" value,
    priced once at read time). No component here re-derives a historical
    value using a different day's price."""
    price = sol_usd_price()
    unrealized_pnl_usd = _current_unrealised_pnl_sol(app, telegram_id) * price
    cumulative_realized_pnl_usd = Decimal(claude_state.load_state(app).get("cumulative_realized_pnl_usd") or "0")
    equity_usd = capital_basis_usd + cumulative_realized_pnl_usd + unrealized_pnl_usd
    return {
        "price_usd": price,
        "unrealized_pnl_usd": unrealized_pnl_usd,
        "cumulative_realized_pnl_usd": cumulative_realized_pnl_usd,
        "equity_usd": equity_usd,
    }


def reset_equity_baseline_after_restart(app, telegram_id, *, capital_basis_usd: Decimal) -> dict:
    """Called only right after a successful claude_state.confirm_restart()
    -- establishes the fresh high-water-mark baseline the owner instruction
    requires, using the SAME authoritative equity function everything else
    uses."""
    equity = compute_current_equity_usd(app, telegram_id, capital_basis_usd=capital_basis_usd)
    return claude_state.reset_high_water_to_current(app, current_equity_usd=equity["equity_usd"])


def _send_owner_drawdown_alert(app, *, drawdown_pct: Decimal, drawdown_usd: Decimal, high_water_equity_usd: Decimal, current_equity_usd: Decimal, open_positions: int) -> None:
    from learnerbot import telegram as _telegram
    import time

    owner_id = _owner_id()
    token = str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or not owner_id:
        return
    text = (
        "🛑 <b>CLAUDE BOT HALTED — 20% DRAWDOWN LIMIT REACHED</b>\n"
        f"Current drawdown: <b>{drawdown_pct:.2f}%</b> (${drawdown_usd:.2f})\n"
        f"High-water-mark equity: ${high_water_equity_usd:.2f}\n"
        f"Current equity: ${current_equity_usd:.2f}\n"
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


def _send_owner_health_alert(app, *, reason: str) -> None:
    from learnerbot import telegram as _telegram
    import time

    owner_id = _owner_id()
    token = str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or not owner_id:
        return
    text = (
        "⚠️ <b>CLAUDE BOT AUTO-DISARMED</b>\n"
        f"Reason: {reason}\n"
        f"Timestamp: {int(time.time())}\n\n"
        "Operating state forced to OFF by the periodic health check. Not an owner action -- "
        "send <code>/claude_status</code> to review, then <code>/claude_arm_live CONFIRM</code> "
        "once the underlying issue is resolved."
    )
    try:
        _telegram.send_message(token, owner_id, text, parse_mode="HTML", protect_content=True)
    except Exception as exc:
        print("[claude-health-alert]", type(exc).__name__, str(exc)[:240])


def _check_and_latch_drawdown(app, telegram_id, *, limits, open_positions: int) -> None:
    """Shared by the pre-buy check and the post-sell recheck -- one call
    site for "evaluate equity/HWM, latch + alert if breached". Never raises
    on its own; callers that must block on a breach do so via
    _guarded_buy's own control flow, not this helper (the post-sell caller
    must NOT block the already-completed exit)."""
    equity = compute_current_equity_usd(app, telegram_id, capital_basis_usd=limits.capital_basis_usd)
    result = claude_state.evaluate_drawdown(
        app,
        current_equity_usd=equity["equity_usd"],
        capital_basis_usd=limits.capital_basis_usd,
        max_drawdown_pct=limits.max_drawdown_pct,
    )
    if result["breached"]:
        first = claude_state.latch_drawdown(app, drawdown_pct=result["drawdown_pct"], drawdown_usd=result["drawdown_usd"])
        if first:
            _send_owner_drawdown_alert(
                app,
                drawdown_pct=result["drawdown_pct"],
                drawdown_usd=result["drawdown_usd"],
                high_water_equity_usd=result["high_water_equity_usd"],
                current_equity_usd=result["current_equity_usd"],
                open_positions=open_positions,
            )
    return result


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
    snapshot = position_snapshot(self.app, self.telegram_id)
    proposed_usd = Decimal(str(amount_sol)) * snapshot["price_usd"]

    limits.check_new_position(
        proposed_usd=proposed_usd,
        current_exposure_usd=snapshot["exposure_usd"],
        open_positions=snapshot["open_positions"],
    )

    result = _check_and_latch_drawdown(self.app, self.telegram_id, limits=limits, open_positions=snapshot["open_positions"])
    if result["breached"]:
        raise ExecutionGuardError(
            f"Drawdown {result['drawdown_pct']:.2f}% of high-water equity "
            f"${result['high_water_equity_usd']:.2f} reached/exceeded the owner-approved "
            f"{limits.max_drawdown_pct:.2f}% limit -- HALTED_DRAWDOWN"
        )

    return _original_buy(self, output_mint, amount_sol, reserve_sol)


def _guarded_sell(self, input_mint: str, amount_raw: int) -> dict:
    # Exits remain possible during a drawdown halt or while not armed: reducing
    # risk must never be blocked by an entry-only circuit breaker.
    check_identity_and_signer(self.app, self.telegram_id)
    before_realized_sol = _cumulative_realized_sol(self.app, self.telegram_id)
    result = _original_sell(self, input_mint, amount_raw)
    # Post-sell drawdown recheck (review, 2026-08-26): a loss-realising sell
    # must latch+alert immediately, not wait for the next buy attempt. Never
    # allowed to block or undo the exit that already completed above.
    try:
        after_realized_sol = _cumulative_realized_sol(self.app, self.telegram_id)
        delta_sol = after_realized_sol - before_realized_sol
        limits = risk_engine_guard.RiskLimits.load()
        if delta_sol != 0:
            price = sol_usd_price()
            claude_state.record_realized_pnl(self.app, pnl_usd=delta_sol * price)
        open_positions = _current_live_open_count(self.app, self.telegram_id)
        _check_and_latch_drawdown(self.app, self.telegram_id, limits=limits, open_positions=open_positions)
    except Exception as exc:  # noqa: BLE001
        print("[claude-post-sell-drawdown-check]", type(exc).__name__, str(exc)[:240])
    return result


def install() -> None:
    if not getattr(_executor.SolanaLiveExecutor, "_claude_risk_guard_installed", False):
        _executor.SolanaLiveExecutor.buy = _guarded_buy
        _executor.SolanaLiveExecutor.sell = _guarded_sell
        _executor.SolanaLiveExecutor._claude_risk_guard_installed = True
