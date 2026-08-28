from __future__ import annotations

"""Isolated learner COLD ZONE entry relaxation overlay.

Owner-approved entry profile:
- signal age <= 50s (hard)
- entry deterioration <= 10% (hard)
- actual-size BUY->SELL round-trip <= 3% remains hard in base Cold Zone
- leader historical gross return is telemetry/score only, not a profit hard block
- estimated total costs <= 5% (hard)
- gross move required for +5% net <= 10% (hard)

This module is imported after solana_cold_zone_strategy_patch and changes only
isolated learner opportunity-entry policy. Existing exit/rug/write-off rules stay
owned by the base COLD ZONE patch.
"""

import html
from decimal import Decimal

from . import solana_cold_zone_strategy_patch as _cz

PROFILE = "COLD_ZONE_17AUG_V4_ENTRY_50S_10PCT"
MAX_SIGNAL_AGE_SECONDS = 50
MAX_ENTRY_DETERIORATION_PCT = Decimal("10")
MAX_ESTIMATED_COST_PCT = Decimal("5")
MAX_REQUIRED_GROSS_PCT = Decimal("10")

_BASE_SETTINGS = _cz.settings_cold_zone


def settings_relaxed(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg.update(
        {
            "solana_strategy_profile": PROFILE,
            "max_signal_age_seconds": str(MAX_SIGNAL_AGE_SECONDS),
            "max_entry_deterioration_pct": str(MAX_ENTRY_DETERIORATION_PCT),
            "cold_zone_max_estimated_cost_pct": str(MAX_ESTIMATED_COST_PCT),
            "cold_zone_max_required_gross_pct": str(MAX_REQUIRED_GROSS_PCT),
        }
    )
    return cfg


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


def install() -> None:
    if getattr(_cz, "_cold_zone_relaxed_entry_installed", False):
        return
    _cz.settings_cold_zone = settings_relaxed
    _cz._sol.settings = settings_relaxed
    _cz._profit_test = profit_test_relaxed
    _cz._entry_rejection_message = entry_rejection_message_relaxed
    _cz._cold_zone_relaxed_entry_installed = True
    print(
        "[solana-cold-zone-entry] installed=true "
        f"profile={PROFILE} signal_age<={MAX_SIGNAL_AGE_SECONDS}s "
        f"entry_deterioration<={MAX_ENTRY_DETERIORATION_PCT}% roundtrip<=3% "
        "leader_gross=score_only "
        f"cost_cap<={MAX_ESTIMATED_COST_PCT}% required_gross_cap<={MAX_REQUIRED_GROSS_PCT}%"
    )


install()
