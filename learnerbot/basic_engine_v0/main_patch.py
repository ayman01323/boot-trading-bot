from __future__ import annotations

import threading

from .. import auto_trader as _auto
from .. import cli as _cli
from .. import fast_market as _fast

# Preserve the battle-tested execution implementation. v0 changes orchestration
# only: it removes two redundant admission layers that can starve otherwise safe
# executable routes, while all wallet/user/quarantine/scanner/LIVE/ARMED,
# LiveTrader profit protection, pool-rug and exact pre-broadcast eth_call gates
# remain authoritative inside the preserved implementation.
_LEGACY_EXECUTE = _auto.execute_best_live_opportunity
_LOCK = threading.RLock()
_INSTALLED = False


def execute_best_live_opportunity_v0(app, opportunities):
    """Primary EVM AUTO entrypoint for Basic Engine v0.

    The original implementation remains the sole capital-moving implementation.
    During this bounded call only, v0 bypasses:
      1) product_universe AUTO admission; and
      2) the extra direct_market_min_gas_multiple gate.

    These two filters sit above LiveTrader's own conservative gas + configured
    minimum-net-profit preflight. Every other current safety/control path remains
    unchanged. The temporary substitutions are serialized and always restored.
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
    _auto._basic_engine_v0_main_installed = True
    _fast._basic_engine_v0_main_installed = True
    _cli._basic_engine_v0_main_installed = True
    _INSTALLED = True
    print(
        "[basic-engine-v0-main] installed=true rpc=main_bot_csv "
        "product_universe_admission=bypassed extra_gas_multiple=bypassed "
        "live_trader_safety=preserved pool_rug=preserved prebroadcast_eth_call=preserved"
    )


install()
