from __future__ import annotations

import time
from decimal import Decimal

from . import solana_sibot as _sol


# Protective entry-only gate requested after a real LIVE position became
# liquidity-trapped.  The existing preflight already obtains a BUY quote and a
# reverse token->SOL quote to measure round-trip deterioration.  This layer uses
# those same two quotes and adds explicit entry- and exit-side price-impact
# requirements. It never adds another Jupiter request and can only reject entry.
_sol.DEFAULTS.update({
    "live_entry_require_exit_liquidity_max_bps": (
        "500",
        "Maximum reverse-exit price impact plus reserved slippage allowed before a Solana entry (5% hard cap)",
    ),
})

_HARD_MAX_EXIT_LIQUIDITY_BPS = Decimal("500")


def _quote_price_impact_bps(quote: dict) -> Decimal | None:
    """Match the LIVE execution guard's Jupiter price-impact semantics."""
    if (quote or {}).get("priceImpact") is not None:
        # Swap V2: percentage points, so 4.5 == 4.5% == 450 bps.
        return abs(_sol._dec((quote or {}).get("priceImpact"), 0)) * Decimal(100)
    if (quote or {}).get("priceImpactPct") is not None:
        # Legacy quote API: decimal fraction, so 0.045 == 4.5% == 450 bps.
        return abs(_sol._dec((quote or {}).get("priceImpactPct"), 0)) * Decimal(10_000)
    return None


def _route_telemetry(quote: dict) -> dict:
    """Extract no-cost Jupiter route provenance from a quote already fetched."""
    plan = (quote or {}).get("routePlan") or []
    if not isinstance(plan, list):
        plan = []
    venues = []
    amms = []
    for hop in plan:
        if not isinstance(hop, dict):
            continue
        swap = hop.get("swapInfo") or {}
        if not isinstance(swap, dict):
            swap = {}
        label = str(swap.get("label") or hop.get("label") or "").strip()
        amm = str(swap.get("ammKey") or hop.get("ammKey") or "").strip()
        if label and label not in venues:
            venues.append(label[:80])
        if amm and amm not in amms:
            amms.append(amm[:64])
    # The execution guard treats an absent routePlan as one executable route.
    return {"hops": max(1, len(plan)), "venues": venues, "amm_keys": amms}


def _entry_execution_limit_bps(cfg: dict, hops: int) -> Decimal:
    """Mirror the LIVE order guard's combined impact+slippage ceiling exactly."""
    limit = max(
        Decimal(1),
        _sol._dec(cfg.get("live_max_combined_impact_slippage_bps"), "150"),
    )
    if int(hops) > 1:
        limit = min(
            limit,
            max(Decimal(1), _sol._dec(cfg.get("live_multihop_max_combined_bps"), "100")),
        )
    return limit


def _entry_exit_liquidity_limit_bps(cfg: dict) -> Decimal:
    configured = max(
        Decimal(1),
        _sol._dec(cfg.get("live_entry_require_exit_liquidity_max_bps"), "500"),
    )
    # Entry must never be allowed on a looser liquidity assumption than the
    # automatic emergency exit can later tolerate.  Both are also hard-capped at
    # 500 bps here, so a per-user/config override cannot silently weaken this new
    # prevention gate above the requested 5% ceiling.
    emergency = max(
        Decimal(1),
        _sol._dec(cfg.get("live_emergency_exit_max_combined_bps"), "500"),
    )
    return min(_HARD_MAX_EXIT_LIQUIDITY_BPS, configured, emergency)


def validate_entry_with_exit_liquidity(app, event: dict, allocation_sol: Decimal, cfg: dict):
    """Run the existing two-quote preflight with entry + reverse liquidity guards.

    BUY-side impact is checked immediately on the first quote against the exact
    LIVE execution ceiling.  A pool that cannot execute the intended allocation
    safely is rejected before the signal is claimed and before any /order request.
    The reverse quote is then reused for exit-liquidity and round-trip checks.
    """
    age = max(0, int(time.time()) - int(event["event_ts"]))
    if age > _sol._int(cfg.get("max_signal_age_seconds"), 30):
        return False, f"stale signal {age}s", {}

    allocation = Decimal(str(allocation_sol))
    lamports = int(allocation * Decimal(1_000_000_000))
    buy_quote = _sol.jupiter_quote(app, _sol.WSOL_MINT, event["mint"], lamports)
    out_raw = _sol._int(buy_quote.get("outAmount") or buy_quote.get("outputAmount"), 0)
    if out_raw <= 0:
        return False, "entry quote returned no token output", {}

    slippage_bps = max(Decimal(0), _sol._dec(cfg.get("live_order_slippage_bps"), "50"))
    buy_route = _route_telemetry(buy_quote)
    buy_impact_bps = _quote_price_impact_bps(buy_quote)
    # The runtime order guard treats an omitted impact field as zero, so use the
    # same semantics here. A later /order quote remains independently guarded.
    effective_buy_impact_bps = buy_impact_bps if buy_impact_bps is not None else Decimal(0)
    entry_limit_bps = _entry_execution_limit_bps(cfg, buy_route["hops"])
    entry_combined_bps = effective_buy_impact_bps + slippage_bps
    detail = {
        "out_raw": out_raw,
        "entry_price_impact_bps": buy_impact_bps,
        "entry_reserved_slippage_bps": slippage_bps,
        "entry_combined_bps": entry_combined_bps,
        "entry_liquidity_limit_bps": entry_limit_bps,
        "entry_route_hops": buy_route["hops"],
        "entry_route_venues": buy_route["venues"],
        "entry_route_amm_keys": buy_route["amm_keys"],
    }
    if entry_combined_bps > entry_limit_bps:
        return False, (
            f"entry liquidity rejected: price impact {effective_buy_impact_bps:.2f} bps + "
            f"slippage reserve {slippage_bps:.0f} bps = {entry_combined_bps:.2f} bps exceeds "
            f"{entry_limit_bps:.0f} bps"
        ), detail

    reverse_quote = _sol.jupiter_quote(app, event["mint"], _sol.WSOL_MINT, out_raw)
    reverse_out_raw = _sol._int(reverse_quote.get("outAmount") or reverse_quote.get("outputAmount"), 0)
    reverse_impact_bps = _quote_price_impact_bps(reverse_quote)
    limit_bps = _entry_exit_liquidity_limit_bps(cfg)
    reverse_route = _route_telemetry(reverse_quote)
    detail.update({
        "reverse_exit_out_lamports": reverse_out_raw,
        "reverse_exit_price_impact_bps": reverse_impact_bps,
        "reverse_exit_reserved_slippage_bps": slippage_bps,
        "reverse_exit_liquidity_limit_bps": limit_bps,
        "reverse_route_hops": reverse_route["hops"],
        "reverse_route_venues": reverse_route["venues"],
        "reverse_route_amm_keys": reverse_route["amm_keys"],
    })

    # Fail closed if Jupiter cannot prove the reverse-side price impact.  An
    # unreported impact must not be treated as zero liquidity risk.
    if reverse_impact_bps is None:
        return False, "reverse exit liquidity unavailable: Jupiter did not report price impact", detail

    combined_bps = reverse_impact_bps + slippage_bps
    detail["reverse_exit_combined_bps"] = combined_bps
    if combined_bps > limit_bps:
        return False, (
            f"reverse exit liquidity rejected: price impact {reverse_impact_bps:.2f} bps + "
            f"slippage reserve {slippage_bps:.0f} bps = {combined_bps:.2f} bps exceeds "
            f"{limit_bps:.0f} bps"
        ), detail

    back_sol = Decimal(reverse_out_raw) / Decimal(1_000_000_000)
    roundtrip = (
        max(Decimal(0), (Decimal(1) - back_sol / allocation) * Decimal(100))
        if allocation > 0
        else Decimal(100)
    )
    detail["roundtrip_loss_pct"] = roundtrip
    if roundtrip > _sol._dec(cfg.get("max_roundtrip_loss_pct"), 3):
        return False, f"round-trip loss {roundtrip:.3f}%", detail

    leader_sol = _sol._dec(event.get("sol_amount"), 0)
    leader_raw = Decimal(int(event.get("token_amount_raw") or 0))
    deterioration = Decimal(0)
    if leader_sol > 0 and leader_raw > 0 and out_raw > 0:
        leader_raw_per_sol = leader_raw / leader_sol
        ours_raw_per_sol = Decimal(out_raw) / allocation
        deterioration = max(
            Decimal(0),
            (leader_raw_per_sol / ours_raw_per_sol - Decimal(1)) * Decimal(100),
        )
        if deterioration > _sol._dec(cfg.get("max_entry_deterioration_pct"), 2):
            detail["deterioration_pct"] = deterioration
            return False, f"entry deterioration {deterioration:.3f}%", detail

    detail["deterioration_pct"] = deterioration
    return True, "PASS_EXIT_LIQUIDITY", detail


def install() -> None:
    if getattr(_sol, "_entry_exit_liquidity_preflight_installed", False):
        return
    _sol._validate_shadow_entry = validate_entry_with_exit_liquidity
    _sol._entry_exit_liquidity_preflight_installed = True
    print(
        "[solana-entry-exit-liquidity] buy_and_reverse_quote_reused=true fail_closed=true "
        "entry_execution_cap_mirrored=true reverse_combined_hard_cap_bps=500 route_telemetry=true"
    )


install()