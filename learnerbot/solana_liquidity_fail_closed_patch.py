from __future__ import annotations

from . import solana_execution_efficiency_patch as _eff
from . import solana_live_executor as _exec
from . import solana_sibot as _sol

_PREV_VALIDATE_ORDER = _eff._validate_order

_sol.DEFAULTS.update({
    "live_require_price_impact_quote": (
        "true",
        "Reject LIVE swaps when Jupiter does not expose route-level price impact",
    ),
})


def validate_order_fail_closed_on_unknown_liquidity(
    executor,
    order: dict,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    trade_value_lamports: int,
    fee_cap_lamports: int,
    cfg: dict,
) -> dict:
    require_impact = _eff._bool(cfg.get("live_require_price_impact_quote"), True)
    if require_impact and order.get("priceImpact") is None and order.get("priceImpactPct") is None:
        _eff._reject(
            executor,
            "route price impact is unavailable; liquidity cannot be proven within LIVE limits",
            input_mint=input_mint,
            output_mint=output_mint,
            amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports,
            fee_cap_lamports=fee_cap_lamports,
            details={"liquidity_guard": "FAIL_CLOSED_MISSING_PRICE_IMPACT"},
        )
    return _PREV_VALIDATE_ORDER(
        executor,
        order,
        input_mint,
        output_mint,
        amount_raw,
        trade_value_lamports,
        fee_cap_lamports,
        cfg,
    )


def install():
    _eff._validate_order = validate_order_fail_closed_on_unknown_liquidity
    print("[solana-liquidity-guard] missing_price_impact=reject route_level_simulation_required=true")


install()
