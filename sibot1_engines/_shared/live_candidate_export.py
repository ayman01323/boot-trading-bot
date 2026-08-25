from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import runtime as _runtime

_INSTALLED = False
_ORIG_TRADE = _runtime.SiBot1ShadowRuntime._handle_trade
_ORIG_EXIT = _runtime.SiBot1ShadowRuntime._handle_exit
_MAX_BYTES = 5_000_000


def _append(runtime, payload: dict) -> None:
    path = Path(runtime.runtime_dir) / "live_candidates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            old = path.with_suffix(".jsonl.1")
            try:
                old.unlink()
            except FileNotFoundError:
                pass
            os.replace(path, old)
    except Exception:
        pass
    row = {"schema_version": 1, "exported_epoch": int(time.time()), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _trade(runtime, intent):
    # Run the same central PoolCheck once before the normal SHADOW path so a
    # HARD_BLOCK candidate is never exported to the independent LIVE bridge.
    try:
        decision = runtime.poolcheck.assess_entry(intent)
        verdict = str(decision.verdict or "HARD_BLOCK").upper()
        reasons = list(decision.reasons or ())[:8]
    except Exception as exc:
        verdict = "HARD_BLOCK"
        reasons = [f"POOLCHECK_ERROR:{type(exc).__name__}"]

    before = set(runtime._lot_ids)
    result = _ORIG_TRADE(runtime, intent)
    if verdict == "HARD_BLOCK" or str(intent.side).upper() == "ARBITRAGE":
        return result

    created = sorted(set(runtime._lot_ids) - before)
    if not created:
        # Only paper-filled entries are eligible to become LIVE candidates.
        return result
    lot_id = created[-1]
    try:
        lot = runtime.positions.get(lot_id)
    except Exception:
        return result

    _append(runtime, {
        "candidate_id": str(intent.intent_id),
        "kind": "ENTRY",
        "shadow_lot_id": str(lot_id),
        "engine_id": str(intent.engine_id),
        "engine_version": str(intent.engine_version),
        "strategy_id": str(intent.strategy_id),
        "chain": str(intent.chain).lower(),
        "asset_in": str(intent.asset_in),
        "asset_out": str(intent.asset_out),
        "requested_virtual_input": str(intent.requested_input_amount),
        "intent_created_at_ms": int(intent.created_at_ms),
        "market_event_id": str(intent.market_event_id or ""),
        "poolcheck_verdict": verdict,
        "poolcheck_reasons": reasons,
    })
    return result


def _exit(runtime, intent):
    if not intent.lot_id:
        return _ORIG_EXIT(runtime, intent)
    try:
        before_lot = runtime.positions.get(intent.lot_id)
        before_qty = before_lot.remaining_quantity
        token = str(before_lot.asset)
        chain = str(before_lot.chain).lower()
    except Exception:
        return _ORIG_EXIT(runtime, intent)

    result = _ORIG_EXIT(runtime, intent)
    try:
        after_lot = runtime.positions.get(intent.lot_id)
        after_qty = after_lot.remaining_quantity
    except Exception:
        return result
    if before_qty <= 0 or after_qty >= before_qty:
        return result

    fraction = (before_qty - after_qty) / before_qty
    _append(runtime, {
        "candidate_id": str(intent.intent_id),
        "kind": "EXIT",
        "shadow_lot_id": str(intent.lot_id),
        "engine_id": str(intent.engine_id),
        "engine_version": str(intent.engine_version),
        "strategy_id": str(intent.strategy_id),
        "chain": chain,
        "asset": token,
        "exit_fraction": str(fraction),
        "reason": str(intent.reason or "strategy_exit")[:160],
        "intent_created_at_ms": int(intent.created_at_ms),
    })
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _runtime.SiBot1ShadowRuntime._handle_trade = _trade
    _runtime.SiBot1ShadowRuntime._handle_exit = _exit
    _INSTALLED = True


install()
