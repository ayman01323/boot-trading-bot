from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .csv_config import BasicEngineCsvError, EvmV2DryRunSettings
from .strategies import AtomicArbitrageRoute, AtomicArbitrageSource


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _decimal(value: object, name: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise BasicEngineCsvError(f"{name} must be numeric") from exc


def load_atomic_v2_routes(
    csv_dir: Path,
    settings: EvmV2DryRunSettings,
) -> AtomicArbitrageSource:
    """Load enabled v0 atomic routes from basic_engine_v0_routes.csv.

    CSV columns:
    chain_id,route_id,path,input_amount_native,priority,enabled,description

    `path` is a `>` separated list of token contract addresses. Routes must
    start and end at the wrapped native address configured in chains.csv.
    """

    path = csv_dir / "basic_engine_v0_routes.csv"
    if not path.exists():
        raise BasicEngineCsvError(f"missing CSV: {path}")

    routes: list[AtomicArbitrageRoute] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"chain_id", "route_id", "path", "enabled"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise BasicEngineCsvError(
                "basic_engine_v0_routes.csv missing columns: " + ",".join(sorted(missing))
            )

        for row in reader:
            if not _truthy(row.get("enabled")):
                continue
            if str(row.get("chain_id") or "").strip() != str(settings.chain_id):
                continue

            route_id = str(row.get("route_id") or "").strip()
            if not route_id:
                raise BasicEngineCsvError("enabled route has empty route_id")
            if route_id in seen:
                raise BasicEngineCsvError(f"duplicate enabled route_id: {route_id}")
            seen.add(route_id)

            tokens = tuple(
                item.strip() for item in str(row.get("path") or "").split(">") if item.strip()
            )
            if len(tokens) < 3:
                raise BasicEngineCsvError(f"route {route_id} must contain at least 3 addresses")
            wrapped = settings.wrapped_base_address.lower()
            if tokens[0].lower() != wrapped or tokens[-1].lower() != wrapped:
                raise BasicEngineCsvError(
                    f"route {route_id} must start/end with chains.csv wrapped base address"
                )

            amount_raw = str(row.get("input_amount_native") or "").strip()
            amount = (
                settings.input_amount_native
                if not amount_raw
                else _decimal(amount_raw, f"{route_id}.input_amount_native")
            )
            if amount <= 0:
                raise BasicEngineCsvError(f"{route_id}.input_amount_native must be positive")

            priority_raw = str(row.get("priority") or "0").strip() or "0"
            priority = _decimal(priority_raw, f"{route_id}.priority")
            routes.append(
                AtomicArbitrageRoute(
                    route_id=route_id,
                    chain=settings.chain_slug,
                    path=tokens,
                    input_value=amount,
                    priority=priority,
                    metadata={
                        "csv_description": str(row.get("description") or "").strip(),
                        "v2_router": settings.router_address,
                    },
                )
            )

    return AtomicArbitrageSource(routes)
