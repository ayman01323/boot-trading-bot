from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RpcEndpoint:
    rpc_id: str
    engine_id: str
    chain: str
    provider: str
    transport: str
    scope: str
    priority: int
    enabled: bool
    endpoint_secret_ref: str
    purpose: str


def load_registry(path: str | Path) -> tuple[RpcEndpoint, ...]:
    rows: list[RpcEndpoint] = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(RpcEndpoint(
                rpc_id=str(r.get("rpc_id") or "").strip(),
                engine_id=str(r.get("engine_id") or "*").strip() or "*",
                chain=str(r.get("chain") or "").strip().lower(),
                provider=str(r.get("provider") or "").strip(),
                transport=str(r.get("transport") or "http").strip().lower(),
                scope=str(r.get("scope") or "shared").strip().lower(),
                priority=int(r.get("priority") or 100),
                enabled=str(r.get("enabled") or "1").strip().lower() in {"1","true","yes","on"},
                endpoint_secret_ref=str(r.get("endpoint_secret_ref") or "").strip(),
                purpose=str(r.get("purpose") or "market").strip(),
            ))
    return tuple(rows)


def select_endpoints(rows: tuple[RpcEndpoint, ...], *, engine_id: str, chain: str, mode: str = "HYBRID") -> tuple[RpcEndpoint, ...]:
    chain = chain.lower(); mode = mode.upper()
    eligible = [r for r in rows if r.enabled and r.chain == chain and r.engine_id in {"*", engine_id}]
    if mode == "SHARED":
        eligible = [r for r in eligible if r.scope == "shared"]
    elif mode == "DEDICATED":
        eligible = [r for r in eligible if r.engine_id == engine_id and r.scope == "dedicated"]
    elif mode != "HYBRID":
        raise ValueError("mode must be SHARED, DEDICATED or HYBRID")
    return tuple(sorted(eligible, key=lambda r: (0 if r.engine_id == engine_id else 1, r.priority, r.rpc_id)))
