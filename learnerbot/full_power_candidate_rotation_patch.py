from __future__ import annotations

"""Rotate full-power EVM discovery across the persisted route graph.

The hot scanner previously asked the deterministic graph helpers for only a very
small prefix of routes on every pass. With several enabled EVM chains this could
mean Base repeatedly re-quoted the same handful of unprofitable/reverting cycles
while thousands of already-discovered pools were never sampled.

This patch changes discovery breadth only. It does not change gross-profit rules,
GPT's net-edge floor, PoolCheck/rug checks, wallet simulation, signing, broadcast,
position limits, LIVE/AUTO/ARMED controls, or trade sizing.
"""

import threading
from pathlib import Path
from typing import Any

from . import full_power_scanner as _fp

_ORIGINAL_GRAPH_TRIANGLES = _fp._graph_triangles
_ORIGINAL_V3_TRIANGLES = _fp._v3_triangles
_ORIGINAL_LOAD_KV_SCOPED = _fp.load_kv_scoped
_LOCK = threading.RLock()
_CURSOR: dict[tuple[str, ...], int] = {}
_MIN_CANDIDATE_CHECKS = 120
_MAX_EXPLORATION = 1000
_EXPANSION_FACTOR = 12


def _rotate(key: tuple[str, ...], items: list[Any], take: int) -> list[Any]:
    if not items or take <= 0:
        return []
    n = len(items)
    take = min(int(take), n)
    with _LOCK:
        start = int(_CURSOR.get(key, 0)) % n
        _CURSOR[key] = (start + take) % n
    return [items[(start + idx) % n] for idx in range(take)]


def _rotating_graph_triangles(
    pool_rows,
    chain_id: int,
    factory_address: str,
    wrapped: str,
    token_universe,
    max_checks: int,
):
    take = max(1, int(max_checks))
    explore = min(_MAX_EXPLORATION, max(take, take * _EXPANSION_FACTOR))
    candidates = _ORIGINAL_GRAPH_TRIANGLES(
        pool_rows,
        chain_id,
        factory_address,
        wrapped,
        token_universe,
        explore,
    )
    key = ("v2", str(chain_id), str(factory_address or "").lower())
    return _rotate(key, list(candidates), take)


def _rotating_v3_triangles(pool_rows, wrapped: str, max_paths: int):
    take = max(1, int(max_paths))
    explore = min(_MAX_EXPLORATION, max(take, take * _EXPANSION_FACTOR))
    candidates = _ORIGINAL_V3_TRIANGLES(pool_rows, wrapped, explore)
    first = pool_rows[0] if pool_rows else {}
    key = (
        "v3",
        str(first.get("chain_id") or ""),
        str(first.get("factory_address") or "").lower(),
        str(wrapped or "").lower(),
    )
    return _rotate(key, list(candidates), take)


def _discovery_settings(path, chain_id):
    data = _ORIGINAL_LOAD_KV_SCOPED(path, chain_id)
    if Path(path).name != "auto_trading_settings.csv":
        return data
    out = dict(data)
    try:
        configured = int(float(out.get("fast_market_max_candidate_checks", "60") or 60))
    except Exception:
        configured = 60
    # Increase only how many already-valid graph candidates are sampled. Existing
    # scanner caps and all profitability/risk checks remain authoritative.
    out["fast_market_max_candidate_checks"] = str(
        min(500, max(_MIN_CANDIDATE_CHECKS, configured))
    )
    return out


def install() -> None:
    if getattr(_fp, "_candidate_rotation_installed", False):
        return
    _fp._graph_triangles = _rotating_graph_triangles
    _fp._v3_triangles = _rotating_v3_triangles
    _fp.load_kv_scoped = _discovery_settings
    _fp._candidate_rotation_installed = True
    print(
        "[full-power-candidate-rotation] installed=true min_checks=120 "
        "rotating=true safety_unchanged=true",
        flush=True,
    )


install()
