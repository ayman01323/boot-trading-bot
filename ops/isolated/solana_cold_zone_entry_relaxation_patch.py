from __future__ import annotations

"""Isolated learner COLD ZONE entry relaxation + 5x sell-depth overlay.

Owner-approved entry profile:
- signal age <= 60s (hard)
- entry deterioration <= 10% (hard)
- LIVE trade size = 0.001 SOL
- actual-size BUY->SELL round-trip <= 3% remains hard
- executable SELL depth for 5x the planned token position must also round-trip <= 3% (hard)
- leader historical gross return is telemetry/score only, not a profit hard block
- estimated total costs <= 5% (hard)
- gross move required for +5% net <= 10% (hard)
- BUY/REFUSAL Telegram messages include direct SOL liquidity and USD liquidity telemetry

This module is imported after solana_cold_zone_strategy_patch and changes only
isolated learner opportunity-entry policy. Existing exit/rug/write-off rules stay
owned by the base COLD ZONE patch.
"""

import html
from decimal import Decimal

from . import solana_cold_zone_strategy_patch as _cz

PROFILE = "COLD_ZONE_17AUG_V6_ENTRY_60S_10PCT_001SOL_DEPTH5X"
MAX_SIGNAL_AGE_SECONDS = 60
MAX_ENTRY_DETERIORATION_PCT = Decimal("10")
LIVE_TRADE_SOL = Decimal("0.001")
MAX_ESTIMATED_COST_PCT = Decimal("5")
MAX_REQUIRED_GROSS_PCT = Decimal("10")
SELL_DEPTH_MULTIPLIER = 5
MAX_SELL_DEPTH_ROUNDTRIP_LOSS_PCT = Decimal("3")
_LIQUIDITY_CACHE_SECONDS = 30

_BASE_SETTINGS = _cz.settings_cold_zone
_BASE_COLD_PREFLIGHT = _cz._cold_preflight
_BASE_POOL_HARD_VS_WARNING = _cz._pool_hard_vs_warning
_BASE_QUEUE_NOTICE = _cz._queue_notice

_POOL_LIQUIDITY_CACHE: dict[str, dict] = {}
_SELL_DEPTH_CACHE: dict[str, dict] = {}


def settings_relaxed(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg.update(
        {
            "solana_strategy_profile": PROFILE,
            "max_signal_age_seconds": str(MAX_SIGNAL_AGE_SECONDS),
            "max_entry_deterioration_pct": str(MAX_ENTRY_DETERIORATION_PCT),
            "live_trade_sol": str(LIVE_TRADE_SOL),
            "cold_zone_max_estimated_cost_pct": str(MAX_ESTIMATED_COST_PCT),
            "cold_zone_max_required_gross_pct": str(MAX_REQUIRED_GROSS_PCT),
            "cold_zone_sell_depth_multiplier": str(SELL_DEPTH_MULTIPLIER),
            "cold_zone_sell_depth_max_roundtrip_loss_pct": str(MAX_SELL_DEPTH_ROUNDTRIP_LOSS_PCT),
        }
    )
    return cfg


def _is_sol_token(token: dict | None) -> bool:
    token = token or {}
    address = str(token.get("address") or "")
    symbol = str(token.get("symbol") or "").upper()
    return address == str(_cz._sol.WSOL_MINT) or symbol in {"SOL", "WSOL"}


def _liquidity_metrics_from_pairs(pairs: list[dict]) -> dict:
    total_usd = Decimal(0)
    direct_sol = Decimal(0)
    direct_sol_pair_usd = Decimal(0)
    direct_sol_pairs = 0
    largest_pair_usd = Decimal(0)

    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        pair_usd = _cz._liq_usd(pair)
        total_usd += pair_usd
        largest_pair_usd = max(largest_pair_usd, pair_usd)
        liq = pair.get("liquidity") or {}
        if _is_sol_token(pair.get("baseToken")):
            direct_sol += max(Decimal(0), _cz._d(liq.get("base"), 0))
            direct_sol_pair_usd += pair_usd
            direct_sol_pairs += 1
        elif _is_sol_token(pair.get("quoteToken")):
            direct_sol += max(Decimal(0), _cz._d(liq.get("quote"), 0))
            direct_sol_pair_usd += pair_usd
            direct_sol_pairs += 1

    return {
        "available": bool(pairs),
        "direct_sol_liquidity": direct_sol,
        "direct_sol_pair_liquidity_usd": direct_sol_pair_usd,
        "total_liquidity_usd": total_usd,
        "largest_pair_liquidity_usd": largest_pair_usd,
        "direct_sol_pairs": direct_sol_pairs,
        "pair_count": len(pairs or []),
    }


def _cache_pool_liquidity(mint: str, pairs: list[dict]) -> dict:
    metrics = _liquidity_metrics_from_pairs(pairs)
    _POOL_LIQUIDITY_CACHE[str(mint)] = {"ts": _cz._now(), **metrics}
    return metrics


def _pool_liquidity_for_message(mint: str) -> dict:
    mint = str(mint or "").strip()
    if not mint:
        return {"available": False}
    cached = dict(_POOL_LIQUIDITY_CACHE.get(mint) or {})
    if cached and _cz._now() - int(cached.get("ts") or 0) <= _LIQUIDITY_CACHE_SECONDS:
        return cached
    try:
        pairs = _cz._dex_pairs(mint)
        return _cache_pool_liquidity(mint, pairs)
    except Exception as exc:
        metrics = {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "ts": _cz._now()}
        _POOL_LIQUIDITY_CACHE[mint] = metrics
        return metrics


def pool_hard_vs_warning_with_liquidity(app, event: dict, cfg: dict, pairs: list[dict]):
    ok, warnings, evidence = _BASE_POOL_HARD_VS_WARNING(app, event, cfg, pairs)
    mint = str((event or {}).get("mint") or "")
    liquidity = _cache_pool_liquidity(mint, pairs)
    evidence = dict(evidence or {})
    evidence["cold_zone_pool_liquidity"] = liquidity
    return ok, warnings, evidence


def cold_preflight_with_5x_sell_depth(app, event: dict, allocation: Decimal, cfg: dict):
    ok, reason, detail = _BASE_COLD_PREFLIGHT(app, event, allocation, cfg)
    detail = dict(detail or {})
    if not ok:
        # The base Cold Zone message still contains its original 0.0005 label.
        # Keep the calculation unchanged but report the current configured size.
        reason = str(reason).replace("actual 0.0005-size", f"actual {Decimal(allocation):f}-size")
        return False, reason, detail

    mint = str((event or {}).get("mint") or "")
    one_x_out_raw = max(0, _cz._i(detail.get("out_raw"), 0))
    multiplier = max(1, _cz._i(cfg.get("cold_zone_sell_depth_multiplier"), SELL_DEPTH_MULTIPLIER))
    depth_raw = one_x_out_raw * multiplier
    expected_sol = Decimal(allocation) * Decimal(multiplier)
    loss_limit = max(
        Decimal(0),
        _cz._d(cfg.get("cold_zone_sell_depth_max_roundtrip_loss_pct"), MAX_SELL_DEPTH_ROUNDTRIP_LOSS_PCT),
    )

    if one_x_out_raw <= 0 or depth_raw <= 0 or expected_sol <= 0:
        depth = {
            "sell_depth_multiplier": multiplier,
            "sell_depth_test_sol": expected_sol,
            "sell_depth_passed": False,
            "sell_depth_reason": "missing actual BUY token amount",
        }
        _SELL_DEPTH_CACHE[mint] = {"ts": _cz._now(), **depth}
        return False, f"{multiplier}x sell-depth test could not prove token amount for exit", {**detail, **depth}

    try:
        sell = _cz._sol.jupiter_quote(app, mint, _cz._sol.WSOL_MINT, depth_raw)
        back_lamports = _cz._i(sell.get("outAmount") or sell.get("outputAmount"), 0)
    except Exception as exc:
        depth = {
            "sell_depth_multiplier": multiplier,
            "sell_depth_test_sol": expected_sol,
            "sell_depth_token_raw": depth_raw,
            "sell_depth_passed": False,
            "sell_depth_reason": f"{type(exc).__name__}: {str(exc)[:220]}",
        }
        _SELL_DEPTH_CACHE[mint] = {"ts": _cz._now(), **depth}
        return (
            False,
            f"{multiplier}x sell-depth quote failed for {expected_sol:.6f} SOL-equivalent position: "
            f"{type(exc).__name__}: {str(exc)[:220]}",
            {**detail, **depth},
        )

    if back_lamports <= 0:
        depth = {
            "sell_depth_multiplier": multiplier,
            "sell_depth_test_sol": expected_sol,
            "sell_depth_token_raw": depth_raw,
            "sell_depth_back_sol": Decimal(0),
            "sell_depth_passed": False,
            "sell_depth_reason": "reverse SELL returned no SOL",
        }
        _SELL_DEPTH_CACHE[mint] = {"ts": _cz._now(), **depth}
        return False, f"{multiplier}x sell-depth reverse SELL returned no SOL", {**detail, **depth}

    back_sol = Decimal(back_lamports) / Decimal(1_000_000_000)
    loss_pct = max(Decimal(0), (Decimal(1) - back_sol / expected_sol) * Decimal(100))
    passed = loss_pct <= loss_limit
    depth = {
        "sell_depth_multiplier": multiplier,
        "sell_depth_test_sol": expected_sol,
        "sell_depth_token_raw": depth_raw,
        "sell_depth_back_sol": back_sol,
        "sell_depth_roundtrip_loss_pct": loss_pct,
        "sell_depth_max_roundtrip_loss_pct": loss_limit,
        "sell_depth_passed": passed,
    }
    _SELL_DEPTH_CACHE[mint] = {"ts": _cz._now(), **depth}

    if not passed:
        return (
            False,
            f"{multiplier}x sell-depth test {expected_sol:.6f} SOL -> {back_sol:.6f} SOL; "
            f"loss {loss_pct:.3f}% > {loss_limit:.3f}%",
            {**detail, **depth},
        )

    return True, reason, {**detail, **depth}


def profit_test_relaxed(app, event: dict, allocation: Decimal, cfg: dict, preflight: dict, executor):
    """Keep the +5% target, but do not cap a new pool by leader mean return."""
    leader = _cz._leader_available_gross(app, str(event.get("leader_wallet") or ""), cfg)
    roundtrip = max(Decimal(0), _cz._d(preflight.get("roundtrip_loss_pct"), 100))
    network_pct, fee_detail = _cz._estimated_network_fee_pct(
        executor, str(event.get("mint") or ""), allocation, cfg
    )
    slippage_bps = max(Decimal(0), _cz._d(cfg.get("live_order_slippage_bps"), 50))
    slippage_reserve_pct = slippage_bps * Decimal(2) / Decimal(100)
    costs = roundtrip + network_pct + slippage_reserve_pct
    required = _cz.TARGET_NET_PCT + costs
    available = _cz._d(leader.get("available_gross_pct"), 0)
    leader_score_implied_net = available - costs
    cost_cap = max(Decimal(0), _cz._d(cfg.get("cold_zone_max_estimated_cost_pct"), MAX_ESTIMATED_COST_PCT))
    required_cap = max(
        _cz.TARGET_NET_PCT,
        _cz._d(cfg.get("cold_zone_max_required_gross_pct"), MAX_REQUIRED_GROSS_PCT),
    )

    detail = {
        **leader,
        **fee_detail,
        **{k: v for k, v in (preflight or {}).items() if str(k).startswith("sell_depth_")},
        "leader_gross_is_score_only": True,
        "roundtrip_loss_pct": roundtrip,
        "estimated_network_fee_pct": network_pct,
        "slippage_reserve_pct": slippage_reserve_pct,
        "estimated_total_cost_pct": costs,
        "target_net_pct": _cz.TARGET_NET_PCT,
        "required_gross_pct": required,
        "expected_net_pct": leader_score_implied_net,
        "max_estimated_cost_pct": cost_cap,
        "max_required_gross_pct": required_cap,
    }

    # Original 17-Aug leader rule still requires at least 5 reconstructed closes.
    if int(leader.get("samples") or 0) < 5:
        return False, f"leader profit evidence has {int(leader.get('samples') or 0)} samples; need 5", detail

    if costs > cost_cap:
        return (
            False,
            f"estimated total costs {costs:.3f}% > {cost_cap:.3f}% COLD ZONE cost cap; "
            f"5.00% net would require {required:.3f}% gross",
            detail,
        )

    if required > required_cap:
        return (
            False,
            f"required gross {required:.3f}% > {required_cap:.3f}% COLD ZONE cap to target "
            f"{_cz.TARGET_NET_PCT:.2f}% net",
            detail,
        )

    # Historical leader return remains visible as a score only. It cannot reject
    # a technically executable new-pool trade under the approved hard cost caps.
    return True, "PASS_COST_CAPS_LEADER_GROSS_SCORE_ONLY", detail


def entry_rejection_message_relaxed(pool_age: int | None, reason: str, profit: dict | None = None, warnings: list[str] | None = None) -> str:
    lines = ["❌ <b>COLD ZONE BUY REFUSED</b>"]
    if pool_age is not None:
        lines.append(f"Pool age: <b>{pool_age // 60}m {pool_age % 60}s</b>")
    lines.append(f"Reason: <code>{html.escape(str(reason)[:700])}</code>")
    if profit:
        leader_score = _cz._d(profit.get("available_gross_pct"), 0)
        costs = _cz._d(profit.get("estimated_total_cost_pct"), 0)
        required = _cz._d(profit.get("required_gross_pct"), _cz.TARGET_NET_PCT + costs)
        cost_cap = _cz._d(profit.get("max_estimated_cost_pct"), MAX_ESTIMATED_COST_PCT)
        gross_cap = _cz._d(profit.get("max_required_gross_pct"), MAX_REQUIRED_GROSS_PCT)
        lines.extend(
            [
                f"Requested net profit: <b>{_cz.TARGET_NET_PCT:.2f}%</b>",
                f"Estimated total costs/reserves: <b>{costs:.3f}%</b> (hard cap <b>{cost_cap:.3f}%</b>)",
                f"Minimum gross move required for +5% net: <b>{required:.3f}%</b> (hard cap <b>{gross_cap:.3f}%</b>)",
                f"Leader historical gross score: <b>{leader_score:.3f}%</b> — <i>informational only, not a blocker</i>",
            ]
        )
    if warnings:
        lines.append("Warnings (not blockers): <code>%s</code>" % html.escape(", ".join(sorted(set(warnings)))[:700]))
    return "\n".join(lines)


def _format_liquidity_lines(mint: str) -> list[str]:
    metrics = _pool_liquidity_for_message(mint)
    lines: list[str] = []
    if metrics.get("available"):
        direct_sol = _cz._d(metrics.get("direct_sol_liquidity"), 0)
        total_usd = _cz._d(metrics.get("total_liquidity_usd"), 0)
        direct_count = _cz._i(metrics.get("direct_sol_pairs"), 0)
        if direct_count > 0:
            lines.append(f"Pool direct SOL liquidity: <b>{direct_sol:.6f} SOL</b>")
        else:
            lines.append("Pool direct SOL liquidity: <b>not reported — routed/non-SOL pool</b>")
        lines.append(f"Pool liquidity (all DexScreener pairs): <b>${total_usd:,.2f}</b>")
    else:
        lines.append("Pool direct SOL liquidity: <b>unavailable</b>")
        lines.append("Pool liquidity USD: <b>unavailable</b>")

    depth = dict(_SELL_DEPTH_CACHE.get(str(mint)) or {})
    if depth:
        multiplier = _cz._i(depth.get("sell_depth_multiplier"), SELL_DEPTH_MULTIPLIER)
        test_sol = _cz._d(depth.get("sell_depth_test_sol"), LIVE_TRADE_SOL * SELL_DEPTH_MULTIPLIER)
        passed = bool(depth.get("sell_depth_passed"))
        if depth.get("sell_depth_back_sol") is not None:
            back_sol = _cz._d(depth.get("sell_depth_back_sol"), 0)
            loss = _cz._d(depth.get("sell_depth_roundtrip_loss_pct"), 100)
            status = "✅ PASS" if passed else "❌ FAIL"
            lines.append(
                f"{multiplier}× executable sell-depth: <b>{status}</b> — "
                f"{test_sol:.6f} SOL-equivalent → {back_sol:.6f} SOL ({loss:.3f}% loss)"
            )
        else:
            status = "✅ PASS" if passed else "❌ FAIL"
            why = html.escape(str(depth.get("sell_depth_reason") or "quote unavailable")[:240])
            lines.append(f"{multiplier}× executable sell-depth: <b>{status}</b> — {why}")
    return lines


def queue_notice_with_pool_liquidity(app, tid: str, kind: str, message: str, position_id: str = "", mint: str = "") -> None:
    kind = str(kind or "")
    if kind in {"BUY_REFUSED", "BUY_CONFIRMED"} and str(mint or ""):
        extra = _format_liquidity_lines(str(mint))
        if extra:
            message = str(message).rstrip() + "\n" + "\n".join(extra)
    return _BASE_QUEUE_NOTICE(app, tid, kind, message, position_id, mint)


def install() -> None:
    if getattr(_cz, "_cold_zone_relaxed_entry_installed", False):
        return
    _cz.settings_cold_zone = settings_relaxed
    _cz._sol.settings = settings_relaxed
    _cz._cold_preflight = cold_preflight_with_5x_sell_depth
    _cz._pool_hard_vs_warning = pool_hard_vs_warning_with_liquidity
    _cz._profit_test = profit_test_relaxed
    _cz._entry_rejection_message = entry_rejection_message_relaxed
    _cz._queue_notice = queue_notice_with_pool_liquidity
    _cz._cold_zone_relaxed_entry_installed = True
    print(
        "[solana-cold-zone-entry] installed=true "
        f"profile={PROFILE} signal_age<={MAX_SIGNAL_AGE_SECONDS}s "
        f"entry_deterioration<={MAX_ENTRY_DETERIORATION_PCT}% live_trade={LIVE_TRADE_SOL}SOL "
        f"roundtrip<=3% sell_depth={SELL_DEPTH_MULTIPLIER}x<=3% "
        "leader_gross=score_only "
        f"cost_cap<={MAX_ESTIMATED_COST_PCT}% required_gross_cap<={MAX_REQUIRED_GROSS_PCT}% "
        "pool_liquidity_telegram=SOL+USD"
    )


install()
