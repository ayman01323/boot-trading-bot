from __future__ import annotations

from learnerbot.config import AppSettings

from .adapters import EvmV2ReadOnlyAdapter, NoBroadcastExecutor
from .core import EngineConfig
from .csv_config import load_evm_v2_dry_run_settings
from .engine import BasicTradingEngine
from .evm_v2_csv import load_atomic_v2_routes
from .strategies import AtomicArbitragePolicy, AtomicArbitrageRiskGate


def build_csv_evm_v2_dry_run_engine(
    app: AppSettings,
    chain_slug: str,
) -> BasicTradingEngine:
    """Build the isolated v0 engine entirely from CSV configuration.

    Sources:
    - chains.csv
    - rpc_endpoints.csv
    - dex_registry.csv
    - basic_engine_v0_settings.csv
    - basic_engine_v0_routes.csv

    The returned engine always has core execution disabled and uses a sentinel
    executor that has no broadcast implementation.
    """

    settings = load_evm_v2_dry_run_settings(app, chain_slug)
    source = load_atomic_v2_routes(app.csv_dir, settings)
    adapter = EvmV2ReadOnlyAdapter(settings)
    economics = AtomicArbitrageRiskGate(
        AtomicArbitragePolicy(
            min_net_profit=settings.min_net_profit_native,
            safety_buffer=settings.safety_buffer_native,
            max_price_impact_bps=settings.max_price_impact_bps,
        )
    )
    return BasicTradingEngine(
        source=source,
        quoter=adapter,
        simulator=adapter,
        executor=NoBroadcastExecutor(),
        risk_gates=[economics],
        config=EngineConfig(
            execution_enabled=False,
            min_expected_profit=settings.min_net_profit_native,
            require_same_route_on_recheck=True,
        ),
    )
