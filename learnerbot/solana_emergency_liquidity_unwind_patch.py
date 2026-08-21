from __future__ import annotations

import html
import json
import time
from contextlib import closing
from contextvars import ContextVar
from decimal import Decimal

from . import solana_execution_efficiency_patch as _eff
from . import solana_exit_circuit_breaker_patch as _exit
from . import solana_liquidity_fail_closed_patch as _liquidity
from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol


# The ordinary route guard is intentionally strict for entries and profit exits.
# A stop-loss, however, must not become permanently unsellable merely because the
# whole position is too large for the remaining pool depth.  Emergency exits may
# use a slightly wider *hard* impact ceiling and, if the whole-position quote is
# still unsafe, progressively try smaller pre-broadcast slices.  A 100% impact
# quote is never bypassed automatically.
#
# If liquidity never recovers (a genuinely drained/rugged pool rather than a
# transient dip), this used to retry forever with no way out and no distinct
# signal that it had become chronic. This layer adds: (1) durable backoff state
# so retry timing survives a process restart instead of resetting to attempt 1,
# (2) a one-per-window escalation alert once a position has been stuck past a
# configurable duration, and (3) an operator-confirmed manual override that can
# accept a much larger (but still capped, never ~100%) loss to actually resolve
# a position the automatic path can never safely close on its own.
_sol.DEFAULTS.update({
    "live_emergency_exit_max_combined_bps": (
        "500",
        "Maximum price-impact plus slippage allowed for loss-driven Solana emergency exits (5%)",
    ),
    "live_emergency_exit_retry_seconds": (
        "60",
        "Initial retry delay after all safe emergency-exit slice sizes are liquidity-blocked",
    ),
    "live_emergency_exit_max_retry_seconds": (
        "900",
        "Maximum exponential retry delay for a liquidity-blocked emergency exit",
    ),
    "live_emergency_exit_escalation_hours": (
        "24",
        "Hours a Solana emergency exit must stay liquidity-blocked before a distinct escalation alert (with the manual-override command) is sent",
    ),
    "live_manual_force_exit_max_combined_bps": (
        "9500",
        "Maximum price-impact plus slippage allowed for an operator-confirmed manual forced Solana exit (up to 95%); a genuine ~100% impact quote is still refused",
    ),
})

_LOSS_EXIT_REASONS = {
    "SOLANA_STOP_LOSS",
    "SOLANA_LEADER_EXIT_LOSS_CAP",
}
_MANUAL_FORCE_REASON = "SOLANA_MANUAL_FORCE_EXIT"
_SLICE_FRACTIONS = (Decimal("1"), Decimal("0.75"), Decimal("0.50"), Decimal("0.25"))
_EXIT_REASON = ContextVar("solana_emergency_exit_reason", default="")

# Importing the two modules above installs the existing fail-closed liquidity
# validator and exit circuit first.  Capture those exact audited implementations
# and wrap them without changing the transaction/broadcast layer beneath them.
_BASE_VALIDATE = _eff._validate_order
_BASE_CLOSE = _exit.close_live_guarded


def _is_loss_exit(reason: str) -> bool:
    return str(reason or "").upper() in _LOSS_EXIT_REASONS


def _emergency_limit(cfg: dict) -> Decimal:
    configured = max(
        Decimal(1),
        _eff._d(cfg.get("live_emergency_exit_max_combined_bps"), "500"),
    )
    # Never make an emergency exit *stricter* than an explicitly wider platform
    # single-route setting.  The default remains 500 bps versus the ordinary
    # 100-150 bps guard.
    ordinary = max(
        Decimal(1),
        _eff._d(cfg.get("live_max_combined_impact_slippage_bps"), "150"),
    )
    return max(configured, ordinary)


def _manual_force_limit(cfg: dict) -> Decimal:
    configured = max(Decimal(1), _eff._d(cfg.get("live_manual_force_exit_max_combined_bps"), "9500"))
    # Hard-capped below ~100% regardless of configuration: a genuine ~100%
    # impact quote discards the asset for essentially zero real value while
    # still paying network fees, which is never a decision the platform should
    # make easy to reach even under an explicit manual confirmation.
    return min(Decimal("9900"), configured)


def validate_order_with_emergency_liquidity(
    executor,
    order: dict,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    trade_value_lamports: int,
    fee_cap_lamports: int,
    cfg: dict,
) -> dict:
    """Preserve normal guards, widening only the impact ceiling for loss exits.

    Fee caps, route-level price-impact presence, RFQ exclusion, slippage, rent and
    execution validation remain unchanged.  Only the combined impact ceiling is
    raised -- to the emergency hard limit for an automatic loss exit, or to the
    wider manual-override limit for an operator-confirmed forced exit.  A genuine
    ~100% price-impact quote is never reachable through either path.
    """
    reason = str(_EXIT_REASON.get() or "").upper()
    effective_cfg = dict(cfg or {})
    is_sell = _eff._action(input_mint, output_mint) == "SELL"
    if is_sell and reason in _LOSS_EXIT_REASONS:
        limit = _emergency_limit(effective_cfg)
        effective_cfg["live_max_combined_impact_slippage_bps"] = str(limit)
        # During a forced loss exit a multi-hop route is not allowed to silently
        # fall back to the ordinary 100 bps ceiling; the same hard ceiling
        # applies to the complete quoted route.
        effective_cfg["live_multihop_max_combined_bps"] = str(limit)
    elif is_sell and reason == _MANUAL_FORCE_REASON:
        limit = _manual_force_limit(effective_cfg)
        effective_cfg["live_max_combined_impact_slippage_bps"] = str(limit)
        effective_cfg["live_multihop_max_combined_bps"] = str(limit)
    return _BASE_VALIDATE(
        executor,
        order,
        input_mint,
        output_mint,
        amount_raw,
        trade_value_lamports,
        fee_cap_lamports,
        effective_cfg,
    )


def _prebroadcast_liquidity_reject(exc: Exception) -> bool:
    text = str(exc or "").lower()
    if "economic execution guard" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "quoted price impact",
            "atomic /build route is",
            "multi-leg atomic route deterioration+slippage",
        )
    )


def _fractions(requested) -> list[Decimal]:
    try:
        f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(requested))))
    except Exception:
        f = Decimal(1)
    if f < Decimal("0.999"):
        return [f]
    return list(_SLICE_FRACTIONS)


def _retry_delay(cfg: dict, attempts: int) -> int:
    base = max(15, min(600, _sol._int(cfg.get("live_emergency_exit_retry_seconds"), 60)))
    maximum = max(base, min(3600, _sol._int(cfg.get("live_emergency_exit_max_retry_seconds"), 900)))
    factor = 2 ** max(0, min(6, int(attempts) - 1))
    return min(maximum, base * factor)


# --- Durable backoff/escalation state -------------------------------------
# Previously an in-memory dict, which reset to empty on every process restart
# (including every deploy), so the exponential backoff never reached its real
# ceiling and a chronically-stuck position looked perpetually "freshly blocked"
# instead of visibly escalating.

def _backoff_key(position_id: str) -> str:
    return f"solana_emergency_backoff:{position_id}"


def _load_backoff(app, position_id: str) -> dict:
    with closing(_sol.connect(app)) as conn:
        raw = _sol._state(conn, _backoff_key(position_id), "") or ""
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_backoff(app, position_id: str, state: dict) -> None:
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, _backoff_key(position_id), json.dumps(state, separators=(",", ":")))


def _clear_backoff(app, position_id: str) -> None:
    _save_backoff(app, position_id, {})


def _backoff_remaining(app, position_id: str) -> int:
    state = _load_backoff(app, position_id)
    return max(0, int(state.get("next_retry", 0)) - int(time.time()))


def _record_liquidity_backoff(app, position_id: str, cfg: dict) -> tuple[int, int, dict]:
    state = _load_backoff(app, position_id)
    attempts = max(0, int(state.get("attempts", 0))) + 1
    delay = _retry_delay(cfg, attempts)
    now = int(time.time())
    new_state = {
        "attempts": attempts,
        "next_retry": now + delay,
        "first_blocked_epoch": int(state.get("first_blocked_epoch") or now),
        "last_escalation_epoch": int(state.get("last_escalation_epoch") or 0),
    }
    _save_backoff(app, position_id, new_state)
    return attempts, delay, new_state


def _short_post_partial_backoff(app, position_id: str) -> None:
    _save_backoff(app, position_id, {"attempts": 0, "next_retry": int(time.time()) + 15})


def _attempt_slices(app, tid, position: dict, fraction, base_reason: str, exit_reason_ctx: str):
    """Try progressively smaller slices under the given exit-reason context.

    Every failed slice is rejected by the economic guard *before* a transaction
    is signed or broadcast, so trying a smaller slice next cannot double-execute
    the position.  Returns (result, None) on the first slice that clears the
    guard, or (None, failures) if every slice remained unsafe.
    """
    failures: list[str] = []
    requested = _fractions(fraction)[0]
    for candidate in _fractions(fraction):
        token = _EXIT_REASON.set(exit_reason_ctx)
        close_reason = str(base_reason)
        if candidate != requested:
            pct = int((candidate * Decimal(100)).to_integral_value())
            close_reason = f"{base_reason}_LIQUIDITY_PARTIAL_{pct}PCT"
        try:
            result = _BASE_CLOSE(app, tid, position, candidate, close_reason)
        except _exec.SolanaLiveError as exc:
            if _prebroadcast_liquidity_reject(exc):
                failures.append(str(exc))
                continue
            raise
        finally:
            _EXIT_REASON.reset(token)
        result = dict(result or {})
        result["liquidity_adaptive_fraction"] = str(candidate)
        return result, None
    return None, failures


def close_live_with_emergency_liquidity_unwind(app, tid, position, fraction, reason):
    """For loss exits, try the largest safe slice without ever selling through 100% impact."""
    if not _is_loss_exit(reason):
        return _BASE_CLOSE(app, tid, position, fraction, reason)

    pid = str((position or {}).get("position_id") or "")
    cfg = dict(_sol.settings(app))
    remaining = _backoff_remaining(app, pid) if pid else 0
    if remaining > 0:
        return {
            "deferred": True,
            "reason": "SOLANA_EMERGENCY_LIQUIDITY_BACKOFF",
            "retry_after_seconds": remaining,
        }

    requested = _fractions(fraction)[0]
    result, failures = _attempt_slices(app, tid, position, fraction, str(reason), str(reason).upper())
    if result is not None:
        candidate = Decimal(result["liquidity_adaptive_fraction"])
        if candidate < requested and not bool(result.get("closed")):
            _short_post_partial_backoff(app, pid)
        else:
            _clear_backoff(app, pid)
        return result

    attempts, delay, state = _record_liquidity_backoff(app, pid, cfg)
    limit = _emergency_limit(cfg)
    last = failures[-1] if failures else "no safe slice quote"
    now = int(time.time())
    first_blocked = int(state.get("first_blocked_epoch") or now)
    stuck_seconds = now - first_blocked
    escalation_hours = max(1, _sol._int(cfg.get("live_emergency_exit_escalation_hours"), 24))
    last_escalation = int(state.get("last_escalation_epoch") or 0)
    should_escalate = stuck_seconds >= escalation_hours * 3600 and (now - last_escalation) >= escalation_hours * 3600

    _live._notify(
        app,
        tid,
        "🧯 <b>Solana emergency exit deferred — liquidity unsafe</b>\n"
        f"Reason: <code>{reason}</code>\n"
        f"Position: <code>{pid}</code>\n"
        f"Hard impact+slippage ceiling: <b>{limit / Decimal(100):.2f}%</b>\n"
        "Tried: <b>100%, 75%, 50% and 25%</b> of the remaining position.\n"
        "No transaction was broadcast. Jupiter still priced every safe slice above the emergency ceiling.\n"
        f"Last guard: <code>{last[:430]}</code>\n"
        f"Automatic retry: <b>{delay}s</b> (liquidity attempt {attempts}).\n"
        "A 100% price-impact quote is not bypassed because that could realise essentially all remaining swap value as loss.",
    )
    if should_escalate:
        state["last_escalation_epoch"] = now
        _save_backoff(app, pid, state)
        days, rem = divmod(stuck_seconds, 86400)
        hours = rem // 3600
        _live._notify(
            app,
            tid,
            "🚨 <b>Solana position stuck "
            f"{days}d {hours}h"
            " — liquidity still unsafe</b>\n"
            f"Position: <code>{pid}</code>  Mint: <code>{html.escape(str((position or {}).get('mint') or ''))}</code>\n"
            "This position has been unable to exit safely for an extended period. Jupiter liquidity for this "
            "token may be permanently gone (a drained/rugged pool), not a transient dip.\n"
            "The bot will keep retrying automatically and will never force a sell through a near-100% price-impact "
            "quote on its own.\n\n"
            "To force an exit yourself and knowingly accept the realised loss (up to "
            f"{(_manual_force_limit(cfg) / Decimal(100)):.0f}% impact+slippage, never a literal ~100% quote), send:\n"
            f"<code>/solanaforceexit {pid} CONFIRM</code>",
        )
    return {
        "deferred": True,
        "reason": "SOLANA_EMERGENCY_LIQUIDITY_BLOCKED",
        "retry_after_seconds": delay,
        "liquidity_attempt": attempts,
        "last_error": last,
        "stuck_seconds": stuck_seconds,
    }


def force_close_live_position(app, tid, position_id: str) -> dict:
    """Operator-confirmed manual exit for a position the automatic path cannot resolve.

    Bypasses the tight automatic emergency ceiling (up to the wider, still-capped
    live_manual_force_exit_max_combined_bps) for exactly one attempt at one named
    position that must already belong to the requesting account and be OPEN. Still
    goes through the same signing/receipt/reconciliation pipeline as every other
    close -- only the impact ceiling is different, and a genuine ~100% impact quote
    remains categorically refused.
    """
    with closing(_sol.connect(app)) as conn:
        row = conn.execute("SELECT * FROM positions WHERE position_id=?", (str(position_id),)).fetchone()
    if not row:
        raise ValueError("Unknown Solana position")
    position = dict(row)
    if str(position.get("telegram_id")) != str(tid):
        raise ValueError("This position does not belong to this account")
    if str(position.get("status") or "").upper() != "OPEN":
        raise ValueError("Position is not open")

    result, failures = _attempt_slices(app, tid, position, Decimal(1), _MANUAL_FORCE_REASON, _MANUAL_FORCE_REASON)
    if result is None:
        last = failures[-1] if failures else "no quote available even at the widened manual ceiling"
        raise ValueError(
            "Every slice still failed the widened manual ceiling; refusing to sell through a "
            f"near-100% price-impact quote. Last guard: {last[:300]}"
        )
    _clear_backoff(app, str(position_id))
    return result


def install():
    if getattr(_sol, "_emergency_liquidity_unwind_installed", False):
        return

    # Keep existing invariant identities truthful by replacing the exported module
    # symbols as well as the live hooks with the same wrappers.
    _liquidity.validate_order_fail_closed_on_unknown_liquidity = validate_order_with_emergency_liquidity
    _eff._validate_order = validate_order_with_emergency_liquidity

    _exit.close_live_guarded = close_live_with_emergency_liquidity_unwind
    _live._close_live = close_live_with_emergency_liquidity_unwind
    _binding._close_bound_live = close_live_with_emergency_liquidity_unwind

    _sol._emergency_liquidity_unwind_installed = True
    print(
        "[solana-emergency-liquidity] loss_exit_cap_bps=500 "
        "adaptive_slices=100,75,50,25 no_impact_bypass=true retry_backoff=true "
        "backoff_persisted=true escalation_hours=24 manual_force_cap_bps=9500"
    )


install()
