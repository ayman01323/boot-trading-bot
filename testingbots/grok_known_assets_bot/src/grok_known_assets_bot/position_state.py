from __future__ import annotations

import json
from typing import Mapping

from .core import Asset, Journal, Position


POSITION_PREFIX = "paper_position:"


def _state_key(asset_key: str) -> str:
    return POSITION_PREFIX + asset_key


def _payload(position: Position) -> dict[str, object]:
    return {
        "asset_key": position.asset_key,
        "chain": position.chain,
        "opened_ts": float(position.opened_ts),
        "entry_price": float(position.entry_price),
        "quantity": float(position.quantity),
        "remaining_quantity": float(position.remaining_quantity),
        "stop_pct": float(position.stop_pct),
        "peak_net_pct": float(position.peak_net_pct),
        "took_tp1": bool(position.took_tp1),
        "trade_id": str(position.trade_id),
        "entry_execution_cost_bps": float(position.entry_execution_cost_bps),
        "paper": True,
    }


def sync_positions(journal: Journal, positions: Mapping[str, Position]) -> None:
    """Persist the complete current PAPER position set and remove stale state."""
    desired = {_state_key(key) for key in positions}
    rows = journal.db.execute(
        "SELECT key FROM state WHERE key LIKE ?",
        (POSITION_PREFIX + "%",),
    ).fetchall()
    existing = {str(row[0]) for row in rows}

    for key, position in positions.items():
        journal.set_state(_state_key(key), _payload(position))

    stale = existing - desired
    if stale:
        journal.db.executemany("DELETE FROM state WHERE key=?", [(key,) for key in sorted(stale)])
        journal.db.commit()


def restore_positions(journal: Journal, assets: Mapping[str, Asset]) -> dict[str, Position]:
    """Restore only complete, valid PAPER position snapshots.

    Historical OPEN rows that pre-date this state format are intentionally not
    reconstructed because they do not contain enough information (notably the
    original stop). They remain in the audit journal but are never guessed back
    into an active position.
    """
    rows = journal.db.execute(
        "SELECT key, value FROM state WHERE key LIKE ? ORDER BY key",
        (POSITION_PREFIX + "%",),
    ).fetchall()
    restored: dict[str, Position] = {}
    invalid_keys: list[str] = []

    for key, raw in rows:
        key = str(key)
        asset_key = key[len(POSITION_PREFIX) :]
        asset = assets.get(asset_key)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("paper") is not True:
                raise ValueError("not a PAPER position snapshot")
            if asset is None or not asset.enabled:
                raise ValueError("asset no longer enabled")
            if str(data.get("asset_key")) != asset_key:
                raise ValueError("asset key mismatch")
            if str(data.get("chain")) != asset.chain:
                raise ValueError("chain mismatch")

            quantity = float(data["quantity"])
            remaining = float(data["remaining_quantity"])
            entry_price = float(data["entry_price"])
            stop_pct = float(data["stop_pct"])
            opened_ts = float(data["opened_ts"])
            if quantity <= 0 or remaining <= 0 or remaining > quantity * 1.000000001:
                raise ValueError("invalid quantity")
            if entry_price <= 0 or stop_pct <= 0 or opened_ts <= 0:
                raise ValueError("invalid numeric position state")

            restored[asset_key] = Position(
                asset_key=asset_key,
                chain=asset.chain,
                opened_ts=opened_ts,
                entry_price=entry_price,
                quantity=quantity,
                remaining_quantity=remaining,
                stop_pct=stop_pct,
                peak_net_pct=float(data.get("peak_net_pct", -999.0)),
                took_tp1=bool(data.get("took_tp1", False)),
                trade_id=str(data.get("trade_id") or ""),
                entry_execution_cost_bps=float(data.get("entry_execution_cost_bps", 0.0)),
            )
            journal.event(
                "POSITION_RECOVERED",
                asset_key,
                {
                    "trade_id": restored[asset_key].trade_id,
                    "remaining_quantity": remaining,
                    "paper": True,
                },
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid_keys.append(key)
            journal.event(
                "POSITION_RECOVERY_REJECT",
                asset_key or None,
                {"reason": f"{type(exc).__name__}:{exc}", "paper": True},
            )

    # Corrupt/stale state must fail closed rather than blocking the runner on
    # every subsequent restart.
    if invalid_keys:
        journal.db.executemany("DELETE FROM state WHERE key=?", [(key,) for key in invalid_keys])
        journal.db.commit()

    return restored
