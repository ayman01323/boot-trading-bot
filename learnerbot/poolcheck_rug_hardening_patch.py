from __future__ import annotations

"""Fail-closed PoolCheck hardening learned from the USD 65k-liquidity rug.

Headline TVL is never a trust signal.  This patch adds holder/creator/LP
concentration evidence, activity telemetry, and a mandatory >=3x reverse-exit
stress probe while preserving the existing LIVE gates and emergency halt.
"""

from decimal import Decimal

from . import evm_pool_rug_gate as _evm
from . import solana_entry_exit_liquidity_preflight_patch as _sol_exit
from . import solana_pool_risk_gate as _sol_pool
from . import solana_preflight_cache_patch as _sol_cache

_MIN_STRESS_EXIT_MULTIPLIER = Decimal("3")
_MAX_STRESS_EXIT_MULTIPLIER = Decimal("5")
_MAX_TOP10_HOLDER_PCT = Decimal("60")
_MAX_SINGLE_HOLDER_PCT = Decimal("20")
_MAX_CREATOR_OWNER_PCT = Decimal("10")
_MAX_UNLOCKED_LP_HOLDER_PCT = Decimal("50")

_INSTALLED = False

_PREV_EVM_GOPLUS = _evm.evaluate_goplus
_PREV_EVM_DEX = _evm.evaluate_dexscreener
_PREV_EVM_EXTERNAL = _evm.external_pool_rug_check
_PREV_EVM_ROUNDTRIP = _evm._manual_roundtrip_check
_PREV_SOL_DEX = _sol_pool.evaluate_dexscreener
_PREV_SOL_VALIDATE = _sol_cache._PREV_VALIDATE
_PREV_SOL_CACHE_KEY = _sol_cache._key


def _pct(value) -> Decimal | None:
    """Normalise GoPlus percentage fields to percentage points."""
    try:
        value = Decimal(str(value))
    except Exception:
        return None
    if value < 0:
        return None
    if value <= 1:
        value *= Decimal(100)
    return max(Decimal(0), min(Decimal(100), value))


def _holder_tag(holder: dict) -> str:
    return " ".join(
        str(holder.get(key) or "").strip().lower()
        for key in ("tag", "label", "name")
    )


def _burn_or_pool_holder(holder: dict) -> bool:
    address = str(holder.get("address") or "").strip().lower()
    if address in {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    }:
        return True
    tag = _holder_tag(holder)
    return any(
        marker in tag
        for marker in ("burn", "dead", "null", "liquidity pool", "lp token", "pair contract")
    )


def _holder_concentration(report: dict) -> tuple[Decimal | None, Decimal | None]:
    holders = report.get("holders")
    if not isinstance(holders, list):
        return None, None
    values: list[Decimal] = []
    for holder in holders[:10]:
        if not isinstance(holder, dict) or _burn_or_pool_holder(holder):
            continue
        pct = _pct(holder.get("percent"))
        if pct is not None:
            values.append(pct)
    if not values:
        return None, None
    return min(Decimal(100), sum(values, Decimal(0))), max(values)


def _creator_owner_control_pct(report: dict) -> Decimal | None:
    creator = _pct(report.get("creator_percent"))
    owner = _pct(report.get("owner_percent"))
    creator_address = str(report.get("creator_address") or "").strip().lower()
    owner_address = str(report.get("owner_address") or "").strip().lower()
    values = [v for v in (creator, owner) if v is not None]
    if not values:
        return None
    if creator_address and owner_address and creator_address == owner_address:
        return max(values)
    return min(Decimal(100), sum(values, Decimal(0)))


def _max_unlocked_lp_pct(report: dict) -> Decimal | None:
    holders = report.get("lp_holders")
    if not isinstance(holders, list):
        return None
    values: list[Decimal] = []
    for holder in holders:
        if not isinstance(holder, dict) or _evm._flag(holder.get("is_locked")):
            continue
        if _burn_or_pool_holder(holder):
            continue
        pct = _pct(holder.get("percent"))
        if pct is not None:
            values.append(pct)
    return max(values) if values else None


def evaluate_goplus_with_concentration(report: dict) -> dict:
    """Run existing token controls, then add economic-control concentration gates."""
    result = _PREV_EVM_GOPLUS(report)
    if not isinstance(result, dict):
        return _evm._decision("HARD_BLOCK", "TOKEN_SECURITY_INVALID", "token-security decision is invalid")

    evidence = result.setdefault("evidence", {})
    top10_pct, max_holder_pct = _holder_concentration(report if isinstance(report, dict) else {})
    creator_owner_pct = _creator_owner_control_pct(report if isinstance(report, dict) else {})
    max_unlocked_lp_pct = _max_unlocked_lp_pct(report if isinstance(report, dict) else {})
    evidence.update(
        {
            "liquidity_amount_is_not_safety_signal": True,
            "holder_count": _evm._int((report or {}).get("holder_count"), 0) if isinstance(report, dict) else 0,
            "lp_holder_count": _evm._int((report or {}).get("lp_holder_count"), 0) if isinstance(report, dict) else 0,
            "top10_holder_pct": top10_pct,
            "max_holder_pct": max_holder_pct,
            "creator_owner_control_pct": creator_owner_pct,
            "max_unlocked_lp_holder_pct": max_unlocked_lp_pct,
        }
    )

    if str(result.get("decision") or "") != "PASS":
        return result
    if _evm._flag((report or {}).get("trust_list")):
        return result

    if max_holder_pct is not None and max_holder_pct > _MAX_SINGLE_HOLDER_PCT:
        return _evm._decision(
            "HARD_BLOCK",
            "HOLDER_CONCENTRATION_RISK",
            f"largest economic token holder controls {max_holder_pct:.2f}% (> {_MAX_SINGLE_HOLDER_PCT:.0f}% hard ceiling)",
            evidence,
        )
    if top10_pct is not None and top10_pct > _MAX_TOP10_HOLDER_PCT:
        return _evm._decision(
            "HARD_BLOCK",
            "HOLDER_CONCENTRATION_RISK",
            f"top-10 economic token holders control {top10_pct:.2f}% (> {_MAX_TOP10_HOLDER_PCT:.0f}% hard ceiling)",
            evidence,
        )
    if creator_owner_pct is not None and creator_owner_pct > _MAX_CREATOR_OWNER_PCT:
        return _evm._decision(
            "HARD_BLOCK",
            "CREATOR_OWNER_CONCENTRATION",
            f"creator/owner economic control is {creator_owner_pct:.2f}% (> {_MAX_CREATOR_OWNER_PCT:.0f}% hard ceiling)",
            evidence,
        )
    return result


def _sum_txns(pairs, window: str) -> tuple[int, int]:
    buys = sells = 0
    if not isinstance(pairs, list):
        return buys, sells
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        entry = ((pair.get("txns") or {}).get(window) or {})
        if isinstance(entry, dict):
            buys += _evm._int(entry.get("buys"), 0)
            sells += _evm._int(entry.get("sells"), 0)
    return buys, sells


def _add_activity_evidence(result: dict, pairs) -> dict:
    if not isinstance(result, dict):
        return result
    evidence = result.setdefault("evidence", {})
    for window in ("m5", "h1", "h6", "h24"):
        buys, sells = _sum_txns(pairs, window)
        evidence[f"dex_txns_{window}_buys"] = buys
        evidence[f"dex_txns_{window}_sells"] = sells
        evidence[f"dex_txns_{window}_total"] = buys + sells
    evidence["liquidity_amount_is_not_safety_signal"] = True
    return result


def evaluate_evm_dex_with_activity(pairs, *args, **kwargs) -> dict:
    return _add_activity_evidence(_PREV_EVM_DEX(pairs, *args, **kwargs), pairs)


def evaluate_solana_dex_with_activity(pairs, *args, **kwargs) -> dict:
    return _add_activity_evidence(_PREV_SOL_DEX(pairs, *args, **kwargs), pairs)


def external_evm_pool_check_with_lp_concentration(trader, token: str) -> dict:
    result = _PREV_EVM_EXTERNAL(trader, token)
    if str((result or {}).get("decision") or "") != "PASS":
        return result

    evidence = result.setdefault("evidence", {})
    youngest = evidence.get("dex_youngest_material_pair_age_seconds")
    max_unlocked = evidence.get("max_unlocked_lp_holder_pct")
    if youngest is not None and float(youngest) < 86400 and max_unlocked is not None:
        if _evm._d(max_unlocked, 0) > _MAX_UNLOCKED_LP_HOLDER_PCT:
            return _evm._decision(
                "HARD_BLOCK",
                "LP_CONCENTRATION_RISK",
                f"young pool has one unlocked LP holder controlling {_evm._d(max_unlocked, 0):.2f}% "
                f"(> {_MAX_UNLOCKED_LP_HOLDER_PCT:.0f}% hard ceiling)",
                evidence,
            )
    return result


def _stress_multiplier(settings: dict | None) -> Decimal:
    raw = (settings or {}).get("live_entry_stress_exit_multiplier", "3")
    try:
        value = Decimal(str(raw))
    except Exception:
        value = _MIN_STRESS_EXIT_MULTIPLIER
    return min(_MAX_STRESS_EXIT_MULTIPLIER, max(_MIN_STRESS_EXIT_MULTIPLIER, value))


def evm_roundtrip_with_stress_exit(trader, token: str, amount_native, expected_token_raw: int) -> dict:
    normal = _PREV_EVM_ROUNDTRIP(trader, token, amount_native, expected_token_raw)
    if str((normal or {}).get("decision") or "") != "PASS":
        return normal

    evidence = dict(normal.get("evidence") or {})
    multiplier = _stress_multiplier(getattr(trader, "settings", {}) or {})
    stress_token_raw = max(1, int(Decimal(int(expected_token_raw)) * multiplier))
    amount_wei = int(_evm._d(amount_native, 0) * Decimal(10**18))
    stress_reference_wei = max(1, int(Decimal(amount_wei) * multiplier))
    evidence.update(
        {
            "stress_exit_multiplier": multiplier,
            "stress_exit_token_raw": stress_token_raw,
            "liquidity_amount_is_not_safety_signal": True,
        }
    )

    try:
        stress_back = int(
            trader.router.functions.getAmountsOut(
                int(stress_token_raw), [token, trader.wrapped]
            ).call()[-1]
        )
    except Exception as exc:
        return _evm._decision(
            "HARD_BLOCK",
            "STRESS_EXIT_ROUTE_FAILED",
            f"{multiplier}x reverse-exit stress quote failed ({type(exc).__name__})",
            evidence,
        )

    stress_loss_bps = max(
        Decimal(0),
        (Decimal(1) - Decimal(stress_back) / Decimal(stress_reference_wei)) * Decimal(10000),
    )
    limit = _evm._d(evidence.get("reference_roundtrip_limit_bps"), _evm._MAX_ROUNDTRIP_LOSS_BPS)
    evidence.update(
        {
            "stress_exit_back_wei": stress_back,
            "stress_exit_roundtrip_loss_bps": stress_loss_bps,
            "stress_exit_roundtrip_limit_bps": limit,
        }
    )
    if stress_back <= 0 or stress_loss_bps > limit:
        return _evm._decision(
            "HARD_BLOCK",
            "STRESS_EXIT_DEPTH",
            f"{multiplier}x reverse-exit stress loses {stress_loss_bps:.1f} bps, above {limit:.1f} bps ceiling",
            evidence,
        )
    return _evm._decision(
        "PASS",
        "REFERENCE_DEPTH_STRESS_PASS",
        f"full-position and {multiplier}x reverse-exit depth probes passed",
        evidence,
    )


def validate_solana_entry_with_stress_exit(app, event: dict, allocation_sol: Decimal, cfg: dict):
    """Preserve the full-position preflight and add one >=3x reverse-exit stress quote."""
    ok, reason, detail = _PREV_SOL_VALIDATE(app, event, allocation_sol, cfg)
    if not ok:
        return ok, reason, detail

    detail = dict(detail or {})
    multiplier = _stress_multiplier(cfg)
    out_raw = _sol_exit._sol._int(detail.get("out_raw"), 0)
    if out_raw <= 0:
        detail["stress_exit_multiplier"] = multiplier
        return False, "stress reverse exit unavailable: full-position token output missing", detail

    stress_raw = max(1, int(Decimal(out_raw) * multiplier))
    detail.update(
        {
            "stress_exit_multiplier": multiplier,
            "stress_reverse_input_token_raw": stress_raw,
            "liquidity_amount_is_not_safety_signal": True,
        }
    )
    try:
        quote = _sol_exit._sol.jupiter_quote(
            app, str(event.get("mint") or ""), _sol_exit._sol.WSOL_MINT, stress_raw
        )
    except Exception as exc:
        return False, f"stress reverse exit quote failed ({type(exc).__name__})", detail

    impact_bps = _sol_exit._quote_price_impact_bps(quote)
    out_lamports = _sol_exit._sol._int(
        quote.get("outAmount") or quote.get("outputAmount"), 0
    )
    slippage_bps = max(
        Decimal(0),
        _sol_exit._sol._dec(
            detail.get("reverse_exit_reserved_slippage_bps"),
            cfg.get("live_order_slippage_bps", "50"),
        ),
    )
    limit_bps = max(
        Decimal(1),
        _sol_exit._sol._dec(
            detail.get("reverse_exit_liquidity_limit_bps"),
            _sol_exit._entry_exit_liquidity_limit_bps(cfg),
        ),
    )
    route = _sol_exit._route_telemetry(quote)
    detail.update(
        {
            "stress_reverse_out_lamports": out_lamports,
            "stress_reverse_price_impact_bps": impact_bps,
            "stress_reverse_reserved_slippage_bps": slippage_bps,
            "stress_reverse_liquidity_limit_bps": limit_bps,
            "stress_reverse_route_hops": route["hops"],
            "stress_reverse_route_venues": route["venues"],
            "stress_reverse_route_amm_keys": route["amm_keys"],
        }
    )
    if impact_bps is None:
        return False, "stress reverse exit liquidity unavailable: Jupiter did not report price impact", detail
    if out_lamports <= 0:
        return False, "stress reverse exit liquidity rejected: quote returned no SOL output", detail

    combined_bps = impact_bps + slippage_bps
    detail["stress_reverse_combined_bps"] = combined_bps
    if combined_bps > limit_bps:
        return False, (
            f"stress reverse exit liquidity rejected: {multiplier}x price impact {impact_bps:.2f} bps + "
            f"slippage reserve {slippage_bps:.0f} bps = {combined_bps:.2f} bps exceeds "
            f"{limit_bps:.0f} bps"
        ), detail

    expected_sol = Decimal(str(allocation_sol)) * multiplier
    recovered_sol = Decimal(out_lamports) / Decimal(1_000_000_000)
    loss_pct = (
        max(Decimal(0), (Decimal(1) - recovered_sol / expected_sol) * Decimal(100))
        if expected_sol > 0
        else Decimal(100)
    )
    detail["stress_roundtrip_loss_pct"] = loss_pct
    max_loss = _sol_exit._sol._dec(cfg.get("max_roundtrip_loss_pct"), "3")
    if loss_pct > max_loss:
        return False, f"stress round-trip loss {loss_pct:.3f}% exceeds {max_loss}% limit", detail

    return True, reason, detail


def solana_preflight_key_with_stress(event, allocation, cfg):
    return _PREV_SOL_CACHE_KEY(event, allocation, cfg) + (
        str(_stress_multiplier(cfg)),
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    _evm.evaluate_goplus = evaluate_goplus_with_concentration
    _evm.evaluate_dexscreener = evaluate_evm_dex_with_activity
    _evm.external_pool_rug_check = external_evm_pool_check_with_lp_concentration
    _evm._manual_roundtrip_check = evm_roundtrip_with_stress_exit

    _sol_pool.evaluate_dexscreener = evaluate_solana_dex_with_activity
    _sol_cache._PREV_VALIDATE = validate_solana_entry_with_stress_exit
    _sol_cache._key = solana_preflight_key_with_stress

    _INSTALLED = True
    print(
        "[poolcheck-rug-hardening] installed=true liquidity_whitelist=false "
        "holder_concentration=true lp_concentration=true activity_telemetry=true "
        "full_position_exit=true stress_exit_multiplier_min=3 fail_closed=true"
    )


install()
