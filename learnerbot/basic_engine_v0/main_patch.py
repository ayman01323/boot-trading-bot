from __future__ import annotations

import threading

from .. import auto_trader as _auto
from .. import cli as _cli
from .. import fast_market as _fast
from .. import full_power_scanner as _full
from . import scanner_patch as _scanner_patch

# Preserve the battle-tested execution implementation. v0 changes orchestration
# only: it removes two redundant admission layers that can starve otherwise safe
# executable routes, while all wallet/user/quarantine/scanner structural checks,
# LIVE/ARMED, LiveTrader profit protection, pool-rug and exact pre-broadcast
# eth_call gates remain authoritative inside the preserved implementation.
_LEGACY_EXECUTE = _auto.execute_best_live_opportunity
_ORIGINAL_SCAN_PRODUCT_POLICY = _full.route_product_policy
_ORIGINAL_SCAN_ALLOWED_PRODUCTS = _full.allowed_product_addresses
_LOCK = threading.RLock()
_INSTALLED = False


def _v0_scanner_product_policy(csv_dir, chain_id, path):
    """Preserve product risk metadata but do not let AUTO admission starve v0.

    The scanner still uses the policy's risk level to tighten price-impact limits.
    Only the upper-level AUTO-admission boolean is bypassed; token/pool safety is
    still enforced by scanner structural/liquidity checks and LiveTrader's pool-rug
    + exact pre-broadcast simulation before capital can move.
    """
    policy = dict(_ORIGINAL_SCAN_PRODUCT_POLICY(csv_dir, chain_id, path) or {})
    policy["auto_trade"] = True
    policy["mode"] = "BASIC_ENGINE_V0_MAIN"
    prior = str(policy.get("reason") or "")
    policy["reason"] = (
        "v0 admission bypass; scanner risk metadata retained"
        + (f"; prior={prior}" if prior else "")
    )
    return policy


def _v0_scanner_allowed_products(csv_dir, chain_id, *, include_shadow=False, max_tokens=None):
    """Never let dynamic product classification hide configured liquid seeds.

    v0 may scan operator-configured seeds even when the dynamic product universe is
    temporarily empty/stale. This does not make a route executable by itself: the
    quote, impact/liquidity, quarantine, wallet simulation, pool-rug and final
    eth_call protections remain mandatory.
    """
    dynamic = list(
        _ORIGINAL_SCAN_ALLOWED_PRODUCTS(
            csv_dir,
            chain_id,
            include_shadow=include_shadow,
            max_tokens=max_tokens,
        )
        or []
    )
    configured = list(_full._configured_token_seeds(csv_dir, int(chain_id)) or [])
    limit = int(max_tokens) if max_tokens is not None else 250
    out = []
    for value in configured + dynamic:
        text = str(value or "").strip()
        if not text:
            continue
        if text.lower() in {x.lower() for x in out}:
            continue
        out.append(text)
        if len(out) >= max(1, limit):
            break
    return out


def execute_best_live_opportunity_v0(app, opportunities):
    """Primary EVM AUTO entrypoint for Basic Engine v0.

    The original implementation remains the sole capital-moving implementation.
    During this bounded call only, v0 bypasses:
      1) product_universe AUTO admission; and
      2) the extra direct_market_min_gas_multiple gate.

    These two filters sit above LiveTrader's own conservative gas + configured
    minimum-net-profit preflight. Every other current safety/control path remains
    unchanged. The temporary execution substitutions are serialized and always
    restored.
    """
    with _LOCK:
        original_product_policy = _auto.route_product_policy
        original_gas_floor = _auto._meets_gas_multiple_floor

        def _v0_product_policy(_csv_dir, _chain_id, _path):
            return {
                "auto_trade": True,
                "mode": "BASIC_ENGINE_V0_MAIN",
                "reason": "v0 delegates product/token safety to scanner approvals + LiveTrader pool-rug preflight",
            }

        def _v0_gas_floor(_sim, _min_gas_multiple):
            # LiveTrader.simulate_cycle already requires capital + a conservative
            # 30% gas reserve + configured minimum net profit before execution.
            return True

        _auto.route_product_policy = _v0_product_policy
        _auto._meets_gas_multiple_floor = _v0_gas_floor
        try:
            return _LEGACY_EXECUTE(app, opportunities)
        finally:
            _auto.route_product_policy = original_product_policy
            _auto._meets_gas_multiple_floor = original_gas_floor


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _auto.execute_best_live_opportunity = execute_best_live_opportunity_v0
    # Both execution owners import the function by value, so bind them explicitly.
    _fast.execute_best_live_opportunity = execute_best_live_opportunity_v0
    _cli.execute_best_live_opportunity = execute_best_live_opportunity_v0

    # The old v0 bypass was applied only inside auto_trader, after the full-power
    # scanner had already filtered the graph and marked routes disabled. Bind the
    # same admission policy at candidate generation so the main engine can actually
    # receive those candidates. Risk-level metadata remains intact.
    _full.route_product_policy = _v0_scanner_product_policy
    _full.allowed_product_addresses = _v0_scanner_allowed_products

    _auto._basic_engine_v0_main_installed = True
    _fast._basic_engine_v0_main_installed = True
    _cli._basic_engine_v0_main_installed = True
    _full._basic_engine_v0_main_installed = True
    _INSTALLED = True
    print(
        "[basic-engine-v0-main] installed=true rpc=main_bot_csv "
        "scanner_product_admission=bypassed configured_seeds=always_visible "
        "v2_discovery_independent=true v3_switch_honoured=true "
        "extra_gas_multiple=bypassed live_trader_safety=preserved "
        "pool_rug=preserved prebroadcast_eth_call=preserved"
    )


install()
