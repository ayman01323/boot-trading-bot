from __future__ import annotations

import re
from decimal import Decimal, ROUND_FLOOR

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_execution_efficiency_patch as _eff
from . import solana_liquidity_fail_closed_patch as _liquidity
from . import solana_sibot as _sol

# Extend only the size search. Every candidate still passes through the same
# pre-broadcast simulation/economic/liquidity stack and the same emergency
# impact+slippage ceiling. A smaller slice is therefore an additional safe quote
# attempt, never a bypass of the existing 500 bps default ceiling.
_SAFE_SLICE_FRACTIONS = (
    Decimal("1"),
    Decimal("0.75"),
    Decimal("0.50"),
    Decimal("0.25"),
    Decimal("0.10"),
    Decimal("0.05"),
    Decimal("0.02"),
    Decimal("0.01"),
)

_sol.DEFAULTS.update({
    "live_emergency_exit_min_net_lamports": (
        "10000",
        "Minimum quoted SOL remaining after conservative exit fees for an automatic emergency slice",
    ),
})

_PREV_VALIDATE = _emergency.validate_order_with_emergency_liquidity
_PREV_LIQUIDITY_REJECT = _emergency._prebroadcast_liquidity_reject
_PREV_NOTIFY = _emergency._live._notify


def _estimated_exit_fee_lamports(cfg: dict) -> int:
    value = max(Decimal(0), _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002"))
    return max(0, int((value * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_FLOOR)))


def _minimum_net_lamports(cfg: dict) -> int:
    # Keep this small and bounded: it is a dust/negative-output guard, not a
    # profitability threshold. It must never turn an otherwise safe loss exit
    # into a strategy-selection decision.
    return max(1_000, min(1_000_000, _sol._int(cfg.get("live_emergency_exit_min_net_lamports"), 10_000)))


def validate_order_with_safe_slice_floor(
    executor,
    order: dict,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    trade_value_lamports: int,
    fee_cap_lamports: int,
    cfg: dict,
) -> dict:
    """Preserve the emergency guard and reject economically-dusty auto slices.

    This wrapper runs *after* the existing emergency liquidity validator has
    accepted the quote. It never raises the impact ceiling. For automatic loss
    exits only, the quoted SOL output must still leave a tiny positive economic
    remainder after the more conservative of Jupiter's fee-equivalent estimate
    and the configured estimated exit fee.
    """
    validated = _PREV_VALIDATE(
        executor,
        order,
        input_mint,
        output_mint,
        amount_raw,
        trade_value_lamports,
        fee_cap_lamports,
        cfg,
    )

    reason = str(_emergency._EXIT_REASON.get() or "").upper()
    is_sell = _eff._action(input_mint, output_mint) == "SELL"
    if not (is_sell and reason in _emergency._LOSS_EXIT_REASONS):
        return validated

    quoted_output = max(0, _sol._int(validated.get("_trade_value_lamports"), trade_value_lamports))
    order_fee = max(0, _sol._int(validated.get("_total_fee_equiv_lamports"), 0))
    conservative_fee = max(order_fee, _estimated_exit_fee_lamports(cfg))
    net_output = quoted_output - conservative_fee
    minimum_net = _minimum_net_lamports(cfg)
    if net_output < minimum_net:
        _eff._reject(
            executor,
            f"net proceeds after fees {net_output} lamports below emergency minimum {minimum_net} lamports",
            input_mint=input_mint,
            output_mint=output_mint,
            amount_raw=amount_raw,
            trade_value_lamports=quoted_output,
            fee_cap_lamports=fee_cap_lamports,
            details={
                "quoted_output_lamports": quoted_output,
                "conservative_fee_lamports": conservative_fee,
                "net_output_lamports": net_output,
                "minimum_net_lamports": minimum_net,
                "emergency_impact_ceiling_bps": str(_emergency._emergency_limit(cfg)),
            },
        )
    return validated


def _jupiter_quote_unavailable(exc_or_text) -> bool:
    """Recognise only Jupiter's explicit no-quote response, not arbitrary HTTP 400s.

    The observed trapped-token failure is a SolanaLiveError containing both
    `Jupiter quote HTTP 400` and Jupiter's structured `Failed to get quotes`
    message. Treating only that exact combination as a pre-broadcast route/
    liquidity failure avoids hiding malformed-request or programming errors.
    """
    text = str(exc_or_text or "").lower()
    return "jupiter quote http 400" in text and "failed to get quotes" in text


def _prebroadcast_liquidity_or_dust_reject(exc: Exception) -> bool:
    if _PREV_LIQUIDITY_REJECT(exc):
        return True
    text = str(exc or "").lower()
    if "economic execution guard" in text and "net proceeds after fees" in text:
        return True
    # No quote means there is no transaction to sign/broadcast. Feed this into
    # the same durable emergency backoff instead of letting the outer position
    # monitor emit a raw Telegram warning every cycle.
    return _jupiter_quote_unavailable(exc)


def _notify_with_quote_unavailable_context(app, tid, text):
    """Make the emergency notice truthful and stable for Jupiter no-quote errors."""
    message = str(text or "")
    lower = message.lower()
    if (
        "solana emergency exit deferred" in lower
        and "jupiter quote http 400" in lower
        and "failed to get quotes" in lower
    ):
        message = message.replace(
            "Solana emergency exit deferred — liquidity unsafe",
            "Solana emergency exit deferred — quote unavailable",
        )
        message = message.replace(
            "No transaction was broadcast. Jupiter still priced every safe slice above the emergency ceiling.",
            "No transaction was broadcast. Jupiter returned no executable quote for the attempted safe slices. "
            "This can indicate exhausted pool liquidity, an unavailable route, or unsupported/malicious token mechanics.",
        )
        # Request IDs are operational noise and change every retry. Keep the
        # owner-facing incident stable/deduplicable while retaining the root cause.
        message = re.sub(
            r"Last guard: <code>.*?</code>",
            "Last guard: <code>Jupiter HTTP 400 — Failed to get quotes</code>",
            message,
            flags=re.DOTALL,
        )
    return _PREV_NOTIFY(app, tid, message)


def install() -> None:
    if getattr(_sol, "_stuck_liquidity_safe_slice_installed", False):
        return

    _emergency._SLICE_FRACTIONS = _SAFE_SLICE_FRACTIONS
    _emergency._prebroadcast_liquidity_reject = _prebroadcast_liquidity_or_dust_reject

    # Keep the invariant identity truthful: the efficiency guard and the
    # fail-closed liquidity export both point at the same outer validator.
    _emergency.validate_order_with_emergency_liquidity = validate_order_with_safe_slice_floor
    _eff._validate_order = validate_order_with_safe_slice_floor
    _liquidity.validate_order_fail_closed_on_unknown_liquidity = validate_order_with_safe_slice_floor

    # Notification-only cleanup for the exact Jupiter no-quote incident. This
    # does not change retry timing, execution, signing, balances or risk gates.
    _emergency._live._notify = _notify_with_quote_unavailable_context

    _sol._stuck_liquidity_safe_slice_installed = True
    print(
        "[solana-stuck-liquidity-safe-slices] adaptive_slices=100,75,50,25,10,5,2,1 "
        "impact_ceiling_unchanged=true min_net_lamports=10000 prebroadcast_only=true "
        "jupiter_no_quote_backoff=true"
    )


install()

# These preflight/RPC protections deliberately load after the established
# emergency/safe-slice stack above. Loss-containment is intentionally NOT loaded
# here: the audited runtime invariant imports it only after leader-quality,
# liquidity-health and easy-exit wrappers have captured their intended inners.
from . import solana_entry_exit_liquidity_preflight_patch as _entry_exit_preflight  # noqa: E402,F401
from . import solana_rpc_exit_priority_patch as _rpc_exit_priority  # noqa: E402,F401
