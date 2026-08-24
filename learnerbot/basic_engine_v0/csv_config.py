from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

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


def _enabled_apex_provider(path: Path, chain_slug: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    matches = []
    for row in rows:
        if (row.get("chain") or "").strip().lower() != chain_slug.strip().lower():
            continue
        if not _bool(row.get("enabled"), False):
            continue
        url = (row.get("rpc_url") or "").strip()
        if not url:
            continue
        try:
            priority = int((row.get("priority") or "999").strip())
        except ValueError:
            priority = 999
        matches.append((priority, row))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return {str(k): str(v or "") for k, v in matches[0][1].items()}


@dataclass(frozen=True)
class EvmV2DryRunSettings:
    chain_id: int
    chain_slug: str
    rpc_url: str
    rpc_provider: str
    rpc_api_key_env: str | None
    rpc_auth_header: str
    wrapped_base_address: str
    router_address: str
    simulation_from: str | None
    enabled: bool
    input_amount_native: Decimal
    min_net_profit_native: Decimal
    safety_buffer_native: Decimal
    max_price_impact_bps: int
    gas_limit_multiplier_bps: int
    fallback_gas_units: int
    deadline_seconds: int
    reference_probe_divisor: int


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
    if not chain.wrapped_base_address:
        raise BasicEngineCsvError(f"wrapped base missing in chains.csv for {chain.slug}")

    provider = _enabled_apex_provider(app.csv_dir / "apex_rpc_providers.csv", chain.slug)
    if provider is not None:
        rpc_url = (provider.get("rpc_url") or "").strip()
        rpc_provider = (provider.get("provider") or "apex_csv").strip() or "apex_csv"
        rpc_api_key_env = (provider.get("api_key_env") or "").strip() or None
    else:
        if not chain.rpc_urls:
            raise BasicEngineCsvError(
                f"no enabled provider in apex_rpc_providers.csv or rpc_endpoints.csv for {chain.slug}"
            )
        rpc_url = chain.rpc_urls[0]
        rpc_provider = "rpc_endpoints_fallback"
        rpc_api_key_env = None

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
    fallback_gas = _int(scoped.get("fallback_gas_units"), 350000, "fallback_gas_units")
    deadline = _int(scoped.get("deadline_seconds"), 120, "deadline_seconds")
    probe_divisor = _int(scoped.get("reference_probe_divisor"), 1000, "reference_probe_divisor")

    if input_amount <= 0:
        raise BasicEngineCsvError("input_amount_native must be greater than zero")
    if min_profit < 0 or safety_buffer < 0:
        raise BasicEngineCsvError("profit and safety buffer cannot be negative")
    if not 0 <= max_impact <= 10_000:
        raise BasicEngineCsvError("max_price_impact_bps must be between 0 and 10000")
    if gas_mult < 10_000:
        raise BasicEngineCsvError("gas_limit_multiplier_bps must be at least 10000")
    if fallback_gas <= 0:
        raise BasicEngineCsvError("fallback_gas_units must be positive")
    if not 30 <= deadline <= 900:
        raise BasicEngineCsvError("deadline_seconds must be between 30 and 900")
    if probe_divisor < 10:
        raise BasicEngineCsvError("reference_probe_divisor must be at least 10")

    return EvmV2DryRunSettings(
        chain_id=chain.chain_id,
        chain_slug=chain.slug,
        rpc_url=rpc_url,
        rpc_provider=rpc_provider,
        rpc_api_key_env=rpc_api_key_env,
        rpc_auth_header=(scoped.get("rpc_auth_header") or "X-API-Key").strip() or "X-API-Key",
        wrapped_base_address=chain.wrapped_base_address,
        router_address=(venue.get("router") or "").strip(),
        simulation_from=(scoped.get("simulation_from") or "").strip() or None,
        enabled=_bool(scoped.get("enabled"), False),
        input_amount_native=input_amount,
        min_net_profit_native=min_profit,
        safety_buffer_native=safety_buffer,
        max_price_impact_bps=max_impact,
        gas_limit_multiplier_bps=gas_mult,
        fallback_gas_units=fallback_gas,
        deadline_seconds=deadline,
        reference_probe_divisor=probe_divisor,
    )
