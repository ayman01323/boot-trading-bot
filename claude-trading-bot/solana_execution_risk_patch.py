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
import threading
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


def _connect_with_retry(app, *, attempts: int = 20, base_delay: float = 0.05, max_delay: float = 0.5):
    """learnerbot.solana_sibot.connect() re-runs `PRAGMA journal_mode=WAL`
    on every single call -- even for a plain read -- which can raise
    sqlite3.OperationalError("database is locked") under genuinely
    concurrent connection opens on the same file, independent of the
    busy_timeout PRAGMA connect() already sets (WAL-mode transition isn't
    always covered by it on every SQLite build). Observed directly under
    this module's own concurrent-sell tests (review, 2026-08-26): with
    several connect() calls per guarded sell and two sells genuinely
    racing, occasional contention is expected, not a sign of a logic bug.
    Retried here, in Claude-owned code only -- NOT a change to learnerbot's
    shared connect() implementation, which stays untouched -- with capped
    exponential backoff generous enough to ride out realistic contention."""
    import sqlite3
    import time as _time

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _sol.connect(app)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            _time.sleep(min(base_delay * (2**attempt), max_delay))
    raise last_exc


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
    2026-08-26, strengthened 2026-08-26): used before every entry, by
    /claude_arm_live and /claude_restart_confirm's precondition recheck, and
    by claude_monitor.py's periodic loop while ARMED -- never re-derived
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
        # The real operator pause/kill switch the running bot actually reads
        # (learnerbot/cli.py, telegram_ui.py, fast_market.py all read this
        # exact key from operator_settings.csv). An earlier version of this
        # check read app.general() instead -- a different CSV that doesn't
        # carry engine_enabled at all, so the check was silently always-on
        # regardless of the real switch. Caught by review, 2026-08-26.
        op = app.operator_settings()
        engine_on = str(op.get("engine_enabled", "true")).strip().lower() in {"1", "true", "yes", "on"}
        if not engine_on:
            return "kill-switch active (operator_settings.engine_enabled=false)"
    except Exception as exc:  # noqa: BLE001
        return f"kill-switch state unreadable: {type(exc).__name__}: {exc}"

    # Composition checks (review, 2026-08-26): buy/sell identity alone isn't
    # proof the whole Claude runtime is intact. Each of these is the exact
    # same structural proof verify_bootstrap_composition.py already
    # established as correct -- reused here, not re-derived differently.
    import claude_bot_quarantine
    import claude_state as _state
    import evm_execution_guard_patch as _evm_guard
    import telegram_control_patch as _router
    from learnerbot import config as _learnerbot_config
    from learnerbot import live_executor as _evm_executor
    from learnerbot import telegram_ui as _ui

    if _learnerbot_config.load_dotenv is not claude_bot_quarantine._noop_load_dotenv:
        return "Claude quarantine is not intact: learnerbot.config.load_dotenv is not the no-op"
    if not _state._INSTALLED:
        return "Claude state machine (claude_state.install()) is not installed"
    if _ui.handle_update is not _router.handle_update:
        return "Telegram router is not installed (learnerbot.telegram_ui.handle_update mismatch)"
    if _executor.SolanaLiveExecutor.buy is not _guarded_buy:
        return "Claude Solana BUY guard is no longer the effective wrapper on SolanaLiveExecutor.buy"
    if _executor.SolanaLiveExecutor.sell is not _guarded_sell:
        return "Claude Solana SELL guard is no longer the effective wrapper on SolanaLiveExecutor.sell"
    # All four EVM signing/broadcast entry points evm_execution_guard_patch.py
    # unconditionally guards -- review (2026-08-26) correctly flagged that
    # checking only .buy left sell/execute_cycle/execute_v3_cycle displacement
    # undetected, so a real EVM broadcast path could go unguarded while this
    # check still reported healthy.
    if _evm_executor.LiveTrader.buy is not _evm_guard._guarded_buy:
        return "EVM execution is no longer denied (buy guard displaced)"
    if _evm_executor.LiveTrader.sell is not _evm_guard._guarded_sell:
        return "EVM execution is no longer denied (sell guard displaced)"
    if _evm_executor.LiveTrader.execute_cycle is not _evm_guard._guarded_execute_cycle:
        return "EVM execution is no longer denied (execute_cycle guard displaced)"
    if _evm_executor.LiveTrader.execute_v3_cycle is not _evm_guard._guarded_execute_v3_cycle:
        return "EVM execution is no longer denied (execute_v3_cycle guard displaced)"
    # Signer path fail-closed: already proven by the check_identity_and_signer()
    # call above -- a second, module-identity-based check here would be both
    # redundant and actively wrong in any test (or future code) that
    # legitimately substitutes signing_interface.get_signer_status for a
    # specific ready/not-ready scenario, which is a normal and correct thing
    # to do, not tampering.
    unpriced = (claude_state.load_state(app).get("unpriced_closed_position_ids") or {})
    if unpriced:
        return (
            f"{len(unpriced)} closed position(s) detected with no trustworthy close-time "
            f"valuation -- equity cannot be trusted until manually reconciled"
        )
    return None


def restart_preconditions(app) -> None:
    """Rechecked before HALTED_DRAWDOWN may clear -- same authoritative
    check ARMED itself depends on. Raises on any failure."""
    reason = armed_health_check(app, _owner_id())
    if reason:
        raise ExecutionGuardError(f"Restart preconditions not met: {reason}")


def _current_live_exposure_sol(app, telegram_id) -> Decimal:
    with closing(_connect_with_retry(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(entry_cost_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _current_live_open_count(app, telegram_id) -> int:
    with closing(_connect_with_retry(app)) as conn:
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
    with closing(_connect_with_retry(app)) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(unrealised_net_sol), 0) AS total FROM positions "
            "WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchone()
        return Decimal(str(row["total"] or 0))


def _closed_live_position_ids_for_mint(app, telegram_id, mint: str) -> set[str]:
    """Scoped to telegram_id + mint (review, 2026-08-26, correcting a real
    race): the earlier version queried every CLOSED position for the owner,
    unscoped. SolanaLiveExecutor.sell() has no execution lock, so a second,
    concurrent sell of a DIFFERENT position could close and appear in this
    call's before/after diff, getting priced with THIS call's sampled rate
    instead of its own -- the exact per-close valuation guarantee this
    whole mechanism exists for. Scoping by mint (all a single sell call can
    ever affect) plus the per-(telegram_id, mint) lock in _guarded_sell()
    together make the diff exact, not merely usually-correct."""
    with closing(_connect_with_retry(app)) as conn:
        rows = conn.execute(
            "SELECT position_id FROM positions WHERE telegram_id=? AND mint=? AND status='CLOSED' AND mode='LIVE'",
            (str(telegram_id), str(mint)),
        ).fetchall()
    return {str(row["position_id"]) for row in rows}


_SELL_LOCKS_GUARD = threading.RLock()
_SELL_LOCKS: dict = {}


def _sell_lock_for(telegram_id, mint: str) -> threading.Lock:
    """One lock per (telegram_id, mint) -- serialises only the narrow
    before-state -> _original_sell -> after-state -> price-capture ->
    account sequence for the SAME mint (review, 2026-08-26). Exits of
    different mints never share a lock and proceed independently; this
    deliberately does not serialise every Claude exit globally."""
    key = (str(telegram_id), str(mint))
    with _SELL_LOCKS_GUARD:
        lock = _SELL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SELL_LOCKS[key] = lock
        return lock


def _account_positions_synchronously(app, telegram_id, position_ids, *, price: Decimal) -> list[dict]:
    """Trustworthy path (review, 2026-08-26, blocker A; correlation fixed
    2026-08-26): called ONLY from _guarded_sell(), for the exact set of
    position_ids its own mint-scoped, lock-serialised SELECT-before/
    SELECT-after id-diff proved closed during THIS call, so `price`,
    fetched immediately after, is close-adjacent -- not a guess made later
    at an arbitrary time, but also not claimed to be mathematically exact
    close-time pricing: no timestamped price is persisted at the execution
    boundary itself (no such column/table exists anywhere in this
    codebase), so a small, bounded gap between broadcast and this sample
    remains. This is the only function in this module allowed to price a
    close and call claude_state.account_closed_position() with that price."""
    if not position_ids:
        return []
    placeholders = ",".join("?" for _ in position_ids)
    with closing(_connect_with_retry(app)) as conn:
        rows = conn.execute(
            f"SELECT position_id, realised_net_sol FROM positions "
            f"WHERE telegram_id=? AND position_id IN ({placeholders})",
            (str(telegram_id), *position_ids),
        ).fetchall()
    accounted = []
    for row in rows:
        pnl_usd = claude_state.account_closed_position(
            app,
            position_id=str(row["position_id"]),
            realised_net_sol=Decimal(str(row["realised_net_sol"] or 0)),
            price_usd_used=price,
        )
        if pnl_usd is not None:
            accounted.append({"position_id": row["position_id"], "pnl_usd": pnl_usd})
    return accounted


def reconcile_realized_pnl(app, telegram_id) -> list[dict]:
    """The fail-closed detection sweep (review, 2026-08-26, blocker A) --
    called from claude_monitor.py's periodic tick, once at process startup
    (claude_state.py's _app wrapper), and as the last step of
    _guarded_sell(). Finds every closed LIVE position this instance's
    ledgers don't know about yet and marks it via
    claude_state.mark_unpriced_closed_position() -- it NEVER prices a close
    itself (that's _account_positions_synchronously()'s job, for the exact
    set a sell call just witnessed close). The learnerbot positions schema
    has no close-time USD/price column, and no price-history table exists
    anywhere in this codebase (checked), so any close this sweep finds is,
    by construction, one the synchronous path never saw -- most likely a
    process crash between the DB commit and that capture running. Guessing
    a price for it (e.g. today's rate) would silently reintroduce exactly
    the currency artifact blocker 1 originally flagged, so this deliberately
    does not. See armed_health_check(), which fails closed while any
    unpriced entry remains -- that is the resolution path (manual
    reconciliation), not an automatic price guess.

    Idempotent regardless of how many times or from how many places this is
    called: an id already in either ledger is always skipped."""
    if not telegram_id:
        return []
    with closing(_connect_with_retry(app)) as conn:
        rows = conn.execute(
            "SELECT position_id, realised_net_sol FROM positions "
            "WHERE telegram_id=? AND status='CLOSED' AND mode='LIVE'",
            (str(telegram_id),),
        ).fetchall()
    state = claude_state.load_state(app)
    known = set(state.get("accounted_position_ids") or {}) | set(state.get("unpriced_closed_position_ids") or {})
    pending = [row for row in rows if str(row["position_id"]) not in known]
    newly_marked = []
    for row in pending:
        marked = claude_state.mark_unpriced_closed_position(
            app,
            position_id=str(row["position_id"]),
            realised_net_sol=Decimal(str(row["realised_net_sol"] or 0)),
        )
        if marked:
            newly_marked.append({"position_id": row["position_id"]})
    return newly_marked


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
    capital basis + a running realised-P&L-in-USD total (each closed
    position accounted exactly once via reconcile_realized_pnl() /
    claude_state.account_closed_position() -- idempotent and crash-safe,
    see those functions) + today's mark-to-market of open positions
    (inherently a "now" value, priced once at read time). No component
    here re-derives a historical value using a different day's price."""
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
    """Shared by the pre-buy check, the post-sell recheck, /claude_status,
    and claude_monitor.py's periodic tick -- one call site for "reconcile
    any newly-closed positions into the ledger, evaluate equity/HWM, latch
    + alert if breached". Never raises on its own; callers that must block
    on a breach do so via their own control flow, not this helper (the
    post-sell caller must NOT block the already-completed exit)."""
    reconcile_realized_pnl(app, telegram_id)
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
    # One authoritative precondition check (review, 2026-08-26) -- covers
    # identity/signer/chain/risk-config/kill-switch/composition AND (this
    # round) unpriced-closed-position fail-closed, instead of a second,
    # partial copy of the same checks living here.
    reason = armed_health_check(self.app, self.telegram_id)
    if reason:
        raise ExecutionGuardError(f"Not safe to enter: {reason}")

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
    # Correlated capture (review, 2026-08-26, correcting a real race the
    # prior version claimed to be immune to but wasn't): scoped to this
    # mint, AND lock-serialised against any other concurrent sell of the
    # SAME mint, so the before/after id-diff below can only ever contain
    # positions THIS call closed -- SolanaLiveExecutor.sell() has no
    # execution lock of its own, and a second thread closing an unrelated
    # position between the two SELECTs would otherwise get swept into this
    # call's price sample. Exits of different mints never share a lock.
    lock = _sell_lock_for(self.telegram_id, input_mint)
    with lock:
        before_closed_ids = _closed_live_position_ids_for_mint(self.app, self.telegram_id, input_mint)
        result = _original_sell(self, input_mint, amount_raw)
        newly_closed_ids: set = set()
        try:
            after_closed_ids = _closed_live_position_ids_for_mint(self.app, self.telegram_id, input_mint)
            newly_closed_ids = after_closed_ids - before_closed_ids
            if newly_closed_ids:
                # Close-adjacent, not mathematically exact close-time pricing --
                # no timestamped price is persisted at the execution boundary
                # itself (checked: no such column/table exists anywhere in this
                # codebase). This is the closest available sample: fetched
                # immediately after the lock-serialised, mint-scoped diff above
                # proves these specific ids closed during this call.
                price = sol_usd_price()
                _account_positions_synchronously(self.app, self.telegram_id, newly_closed_ids, price=price)
        except Exception as exc:  # noqa: BLE001
            # Price capture (or the accounting write) failed after a successful
            # sell -- deliberately NOT retried here and NOT swallowed silently
            # into a guess. Any id in newly_closed_ids that didn't make it into
            # accounted_position_ids stays absent from BOTH ledgers, so the
            # next reconcile_realized_pnl() sweep (monitor tick or startup)
            # will find it and correctly mark it unpriced -- fail closed, not
            # fail silent.
            print("[claude-post-sell-account]", type(exc).__name__, str(exc)[:240])
    # Drawdown recheck (review, 2026-08-26): a loss-realising sell must
    # latch+alert immediately, not wait for the next buy attempt or a
    # crash-recovery pass. Deliberately OUTSIDE the mint lock (no per-mint
    # race concern here, and holding a lock across this would serialise
    # unrelated mints' drawdown checks for no reason) and never allowed to
    # block or undo the exit that already completed above.
    try:
        limits = risk_engine_guard.RiskLimits.load()
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
