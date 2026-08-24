from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from learnerbot.config import AppSettings, load_chains, load_dex_registry, load_kv_scoped


class BasicEngineCsvError(RuntimeError):
    pass


def _decimal(value: str | None, default: str, name: str) -> Decimal:
    raw = default if value is None or str(value).strip() == "" else str(value).strip()
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise BasicEngineCsvError(f"{name} must be numeric") from exc


def _int(value: str | None, default: int, name: str) -> int:
    raw = default if value is None or str(value).strip() == "" else str(value).strip()
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BasicEngineCsvError(f"{name} must be an integer") from exc


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True)
class EvmV2DryRunSettings:
    chain_id: int
    chain_slug: str
    rpc_url: str
    wrapped_base_address: str
    router_address: str
    simulation_from: str | None
    enabled: bool
    input_amount_native: Decimal
    min_net_profit_native: Decimal
    safety_buffer_native: Decimal
    max_price_impact_bps: int
    gas_limit_multiplier_bps: int


def load_evm_v2_dry_run_settings(app: AppSettings, chain_slug: str) -> EvmV2DryRunSettings:
    chain = next(
        (c for c in load_chains(app, enabled_only=False) if c.slug == chain_slug.strip().lower()),
        None,
    )
    if chain is None:
        raise BasicEngineCsvError(f"unknown chain: {chain_slug}")
    if chain.type != "EVM":
        raise BasicEngineCsvError(f"chain is not EVM: {chain.slug}")
    if not chain.enabled:
        raise BasicEngineCsvError(f"chain disabled in chains.csv: {chain.slug}")
    if not chain.rpc_urls:
        raise BasicEngineCsvError(f"no enabled RPC in rpc_endpoints.csv for {chain.slug}")
    if not chain.wrapped_base_address:
        raise BasicEngineCsvError(f"wrapped base missing in chains.csv for {chain.slug}")

    venues = [
        row
        for row in load_dex_registry(app.csv_dir, chain.chain_id)
        if (row.get("version") or "").strip().upper() == "V2"
        and (row.get("router") or "").strip()
    ]
    if not venues:
        raise BasicEngineCsvError(f"no enabled V2 router in dex_registry.csv for {chain.slug}")

    scoped = load_kv_scoped(app.csv_dir / "basic_engine_v0_settings.csv", chain.chain_id)
    router_name = (scoped.get("v2_dex_name") or "").strip().lower()
    if router_name:
        matching = [
            row for row in venues if (row.get("dex_name") or "").strip().lower() == router_name
        ]
        if not matching:
            raise BasicEngineCsvError(
                f"configured v2_dex_name is not enabled in dex_registry.csv for {chain.slug}"
            )
        venue = matching[0]
    else:
        venue = venues[0]

    input_amount = _decimal(scoped.get("input_amount_native"), "0.01", "input_amount_native")
    min_profit = _decimal(scoped.get("min_net_profit_native"), "0", "min_net_profit_native")
    safety_buffer = _decimal(scoped.get("safety_buffer_native"), "0", "safety_buffer_native")
    max_impact = _int(scoped.get("max_price_impact_bps"), 500, "max_price_impact_bps")
    gas_mult = _int(scoped.get("gas_limit_multiplier_bps"), 13000, "gas_limit_multiplier_bps")

    if input_amount <= 0:
        raise BasicEngineCsvError("input_amount_native must be greater than zero")
    if min_profit < 0 or safety_buffer < 0:
        raise BasicEngineCsvError("profit and safety buffer cannot be negative")
    if not 0 <= max_impact <= 10_000:
        raise BasicEngineCsvError("max_price_impact_bps must be between 0 and 10000")
    if gas_mult < 10_000:
        raise BasicEngineCsvError("gas_limit_multiplier_bps must be at least 10000")

    simulation_from = (scoped.get("simulation_from") or "").strip() or None
    return EvmV2DryRunSettings(
        chain_id=chain.chain_id,
        chain_slug=chain.slug,
        rpc_url=chain.rpc_urls[0],
        wrapped_base_address=chain.wrapped_base_address,
        router_address=(venue.get("router") or "").strip(),
        simulation_from=simulation_from,
        enabled=_bool(scoped.get("enabled"), False),
        input_amount_native=input_amount,
        min_net_profit_native=min_profit,
        safety_buffer_native=safety_buffer,
        max_price_impact_bps=max_impact,
        gas_limit_multiplier_bps=gas_mult,
    )
