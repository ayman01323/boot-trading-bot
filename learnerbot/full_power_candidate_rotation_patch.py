from __future__ import annotations

"""Rotate full-power EVM discovery across the persisted route graph.

The hot scanner previously asked deterministic graph helpers for a small prefix of
routes on every pass. With several enabled EVM chains, Base could repeatedly quote
the same unprofitable/reverting cycles while thousands of already-discovered pools
were never sampled.

This patch changes route selection only. It deliberately preserves the configured
quote-call budget, gross-profit rules, GPT net-edge floor, PoolCheck/rug checks,
wallet simulation, signing, broadcast, position limits, LIVE/AUTO/ARMED controls,
and trade sizing. In particular it does not increase RPC pressure while 429s are
present.
"""

import threading
from typing import Any

from . import full_power_scanner as _fp

_ORIGINAL_GRAPH_TRIANGLES = _fp._graph_triangles
_ORIGINAL_V3_TRIANGLES = _fp._v3_triangles
_LOCK = threading.RLock()
_CURSOR: dict[tuple[str, ...], int] = {}
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


def install() -> None:
    if getattr(_fp, "_candidate_rotation_installed", False):
        return
    _fp._graph_triangles = _rotating_graph_triangles
    _fp._v3_triangles = _rotating_v3_triangles
    _fp._candidate_rotation_installed = True
    print(
        "[full-power-candidate-rotation] installed=true rotating=true "
        "quote_budget_unchanged=true safety_unchanged=true",
        flush=True,
    )


install()
