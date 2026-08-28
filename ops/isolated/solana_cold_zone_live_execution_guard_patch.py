from __future__ import annotations

"""Isolated Cold Zone LIVE execution cross-check.

Jupiter's reported ``priceImpact`` is a reference-price metric. On very new Solana
mints it can be extreme even when the executable BUY->SELL round trip is healthy.
Cold Zone already requires both an actual-size reverse quote and a 5x sell-depth
reverse quote before LIVE execution. This patch keeps the normal execution guard
for every trade except a fresh Cold Zone BUY whose two executable depth checks
passed.

For that narrow case only, an anomalous reported Jupiter priceImpact may be
ignored if the signable LIVE order output has deteriorated no more than 100 bps
(1%) versus the just-completed Cold Zone BUY preflight. All fee, rent, route,
transaction, simulation, reserve and later build-deterioration guards remain in
force. SELL paths are never relaxed here.
"""

import time
from decimal import Decimal

from . import solana_cold_zone_entry_relaxation_patch as _entry
from . import solana_cold_zone_strategy_patch as _cz
from . import solana_execution_efficiency_patch as _eff

PROFILE = "COLD_ZONE_LIVE_IMPACT_CROSSCHECK_V1"
PROOF_TTL_SECONDS = 30
MAX_LIVE_ORDER_DERIORATION_BPS = Decimal("100")

_BASE_PREFLIGHT = _cz._cold_preflight
_BASE_VALIDATE_ORDER = _eff._validate_order
_PREFLIGHT_PROOF: dict[str, dict] = {}


def _now() -> int:
    return int(time.time())


def _capture_preflight(app, event: dict, allocation: Decimal, cfg: dict):
    ok, reason, detail = _BASE_PREFLIGHT(app, event, allocation, cfg)
    detail = dict(detail or {})
    mint = str((event or {}).get("mint") or "").strip()
    if ok and mint:
        out_raw = max(0, _cz._i(detail.get("out_raw"), 0))
        depth_passed = bool(detail.get("sell_depth_passed"))
        roundtrip = max(Decimal(0), _cz._d(detail.get("roundtrip_loss_pct"), 100))
        depth_loss = max(Decimal(0), _cz._d(detail.get("sell_depth_roundtrip_loss_pct"), 100))
        if out_raw > 0 and depth_passed:
            _PREFLIGHT_PROOF[mint] = {
                "ts": _now(),
                "out_raw": out_raw,
                "roundtrip_loss_pct": roundtrip,
                "sell_depth_roundtrip_loss_pct": depth_loss,
                "sell_depth_multiplier": max(1, _cz._i(detail.get("sell_depth_multiplier"), 5)),
            }
    return ok, reason, detail


def _fresh_proof(mint: str) -> dict | None:
    proof = dict(_PREFLIGHT_PROOF.get(str(mint)) or {})
    if not proof:
        return None
    if _now() - int(proof.get("ts") or 0) > PROOF_TTL_SECONDS:
        _PREFLIGHT_PROOF.pop(str(mint), None)
        return None
    return proof


def _validate_order_with_cold_zone_crosscheck(
    executor,
    order: dict,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    trade_value_lamports: int,
    fee_cap_lamports: int,
    cfg: dict,
) -> dict:
    # Normal execution policy for SELLs, non-SOL inputs, and any BUY without a
    # fresh executable Cold Zone preflight proof.
    is_buy = str(input_mint) == str(_cz._sol.WSOL_MINT) and str(output_mint) != str(_cz._sol.WSOL_MINT)
    proof = _fresh_proof(str(output_mint)) if is_buy else None
    if not proof:
        return _BASE_VALIDATE_ORDER(
            executor, order, input_mint, output_mint, amount_raw,
            trade_value_lamports, fee_cap_lamports, cfg,
        )

    slippage = Decimal(max(0, _eff._i(order.get("slippageBps"), _eff._i(cfg.get("live_order_slippage_bps"), 50))))
    impact = _eff._price_impact_bps(order)
    hops = _eff._route_hops(order)
    max_combined = max(Decimal(1), _eff._d(cfg.get("live_max_combined_impact_slippage_bps"), 150))
    if hops > 1:
        max_combined = min(max_combined, max(Decimal(1), _eff._d(cfg.get("live_multihop_max_combined_bps"), 100)))
    combined = impact + slippage

    # If the normal impact guard is happy, preserve it unchanged.
    if combined <= max_combined:
        return _BASE_VALIDATE_ORDER(
            executor, order, input_mint, output_mint, amount_raw,
            trade_value_lamports, fee_cap_lamports, cfg,
        )

    live_out = max(0, _eff._i(order.get("outAmount") or order.get("outputAmount"), 0))
    preflight_out = max(0, int(proof.get("out_raw") or 0))
    if live_out <= 0 or preflight_out <= 0:
        return _BASE_VALIDATE_ORDER(
            executor, order, input_mint, output_mint, amount_raw,
            trade_value_lamports, fee_cap_lamports, cfg,
        )

    deterioration_bps = max(
        Decimal(0),
        (Decimal(1) - Decimal(live_out) / Decimal(preflight_out)) * Decimal(10_000),
    )
    max_det = max(
        Decimal(1),
        _eff._d(cfg.get("cold_zone_live_order_vs_preflight_max_bps"), MAX_LIVE_ORDER_DERIORATION_BPS),
    )
    if deterioration_bps > max_det:
        _eff._reject(
            executor,
            (
                f"Cold Zone LIVE order output deteriorated {deterioration_bps:.2f} bps versus "
                f"fresh executable preflight; cap {max_det:.0f} bps"
            ),
            input_mint=input_mint,
            output_mint=output_mint,
            amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports,
            fee_cap_lamports=fee_cap_lamports,
            details={
                "reported_price_impact_bps": str(impact),
                "slippage_bps": str(slippage),
                "preflight_out_raw": str(preflight_out),
                "live_order_out_raw": str(live_out),
                "live_order_deterioration_bps": str(deterioration_bps),
                "proof_roundtrip_loss_pct": str(proof.get("roundtrip_loss_pct")),
                "proof_sell_depth_roundtrip_loss_pct": str(proof.get("sell_depth_roundtrip_loss_pct")),
            },
        )

    # The reported reference-price impact is the only field bypassed. Re-run the
    # entire normal validator with impact neutralised so every other execution
    # guard remains mandatory.
    checked = dict(order)
    checked["priceImpact"] = 0
    checked["priceImpactPct"] = None
    validated = _BASE_VALIDATE_ORDER(
        executor, checked, input_mint, output_mint, amount_raw,
        trade_value_lamports, fee_cap_lamports, cfg,
    )
    validated["_reported_price_impact_bps"] = str(impact)
    validated["_cold_zone_price_impact_crosscheck"] = True
    validated["_cold_zone_preflight_out_raw"] = str(preflight_out)
    validated["_cold_zone_live_order_out_raw"] = str(live_out)
    validated["_cold_zone_live_order_deterioration_bps"] = str(deterioration_bps)
    validated["_cold_zone_preflight_roundtrip_loss_pct"] = str(proof.get("roundtrip_loss_pct"))
    validated["_cold_zone_preflight_sell_depth_loss_pct"] = str(proof.get("sell_depth_roundtrip_loss_pct"))
    return validated


def settings_with_crosscheck(app) -> dict:
    cfg = dict(_entry.settings_relaxed(app))
    cfg["cold_zone_live_order_vs_preflight_max_bps"] = str(MAX_LIVE_ORDER_DERIORATION_BPS)
    cfg["cold_zone_live_price_impact_policy"] = "crosscheck_executable_preflight"
    return cfg


def install() -> None:
    _cz._cold_preflight = _capture_preflight
    _cz.settings_cold_zone = settings_with_crosscheck
    _eff._validate_order = _validate_order_with_cold_zone_crosscheck
    print(
        "[solana-cold-zone-live-guard] installed=true "
        f"profile={PROFILE} reported_price_impact=crosscheck_only "
        f"fresh_preflight_ttl={PROOF_TTL_SECONDS}s live_order_deterioration<={MAX_LIVE_ORDER_DERIORATION_BPS:.0f}bps "
        "roundtrip_hard=UNCHANGED sell_depth_5x_hard=UNCHANGED fee_rent_simulation_reserve=UNCHANGED sells=UNCHANGED"
    )


install()
