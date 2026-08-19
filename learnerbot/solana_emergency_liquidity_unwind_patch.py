from __future__ import annotations

import time
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
# quote is never bypassed.
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
})

_LOSS_EXIT_REASONS = {
    "SOLANA_STOP_LOSS",
    "SOLANA_LEADER_EXIT_LOSS_CAP",
}
_SLICE_FRACTIONS = (Decimal("1"), Decimal("0.75"), Decimal("0.50"), Decimal("0.25"))
_EXIT_REASON = ContextVar("solana_emergency_exit_reason", default="")
_BACKOFF: dict[str, dict[str, int]] = {}

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
    raised to the configured emergency hard limit while the loss-exit context is
    active.  A 100% price-impact quote therefore remains rejected.
    """
    reason = str(_EXIT_REASON.get() or "").upper()
    effective_cfg = dict(cfg or {})
    if reason in _LOSS_EXIT_REASONS and _eff._action(input_mint, output_mint) == "SELL":
        limit = _emergency_limit(effective_cfg)
        effective_cfg["live_max_combined_impact_slippage_bps"] = str(limit)
        # During a forced loss exit a multi-hop route is not allowed to silently
        # fall back to the ordinary 100 bps ceiling; the same 5% hard ceiling
        # applies to the complete quoted route.
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


def _backoff_remaining(position_id: str) -> int:
    state = _BACKOFF.get(str(position_id)) or {}
    return max(0, int(state.get("next_retry", 0)) - int(time.time()))


def _record_liquidity_backoff(position_id: str, cfg: dict) -> tuple[int, int]:
    pid = str(position_id)
    state = _BACKOFF.get(pid) or {}
    attempts = max(0, int(state.get("attempts", 0))) + 1
    delay = _retry_delay(cfg, attempts)
    _BACKOFF[pid] = {
        "attempts": attempts,
        "next_retry": int(time.time()) + delay,
    }
    return attempts, delay


def _short_post_partial_backoff(position_id: str) -> None:
    _BACKOFF[str(position_id)] = {
        "attempts": 0,
        "next_retry": int(time.time()) + 15,
    }


def close_live_with_emergency_liquidity_unwind(app, tid, position, fraction, reason):
    """For loss exits, try the largest safe slice without ever selling through 100% impact.

    Every failed slice here is rejected by the economic guard *before* a transaction
    is signed or broadcast.  Once any slice reaches the broadcast layer, the existing
    exit circuit remains authoritative and this function does not try a second sell.
    """
    if not _is_loss_exit(reason):
        return _BASE_CLOSE(app, tid, position, fraction, reason)

    pid = str((position or {}).get("position_id") or "")
    cfg = dict(_sol.settings(app))
    remaining = _backoff_remaining(pid) if pid else 0
    if remaining > 0:
        return {
            "deferred": True,
            "reason": "SOLANA_EMERGENCY_LIQUIDITY_BACKOFF",
            "retry_after_seconds": remaining,
        }

    failures: list[str] = []
    requested = _fractions(fraction)[0]
    for candidate in _fractions(fraction):
        token = _EXIT_REASON.set(str(reason or "").upper())
        close_reason = str(reason)
        if candidate != requested:
            pct = int((candidate * Decimal(100)).to_integral_value())
            close_reason = f"{reason}_LIQUIDITY_PARTIAL_{pct}PCT"
        try:
            result = _BASE_CLOSE(app, tid, position, candidate, close_reason)
        except _exec.SolanaLiveError as exc:
            # These particular economic rejections happen before signing/sending,
            # so trying a smaller slice cannot double-execute the position.
            if _prebroadcast_liquidity_reject(exc):
                failures.append(str(exc))
                continue
            raise
        finally:
            _EXIT_REASON.reset(token)

        result = dict(result or {})
        result["liquidity_adaptive_fraction"] = str(candidate)
        if candidate < requested and not bool(result.get("closed")):
            _short_post_partial_backoff(pid)
        else:
            _BACKOFF.pop(pid, None)
        return result

    attempts, delay = _record_liquidity_backoff(pid, cfg)
    limit = _emergency_limit(cfg)
    last = failures[-1] if failures else "no safe slice quote"
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
    return {
        "deferred": True,
        "reason": "SOLANA_EMERGENCY_LIQUIDITY_BLOCKED",
        "retry_after_seconds": delay,
        "liquidity_attempt": attempts,
        "last_error": last,
    }


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
        "adaptive_slices=100,75,50,25 no_impact_bypass=true retry_backoff=true"
    )


install()
