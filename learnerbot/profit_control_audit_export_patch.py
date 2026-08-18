from __future__ import annotations

from . import transaction_audit as _audit

_parts = list(_audit.RELEVANT_TABLE_PARTS)
for _part in ("strategy", "control"):
    if _part not in _parts:
        _parts.append(_part)
_audit.RELEVANT_TABLE_PARTS = tuple(_parts)

print("[profit-control-audit] strategy_registry=true control_runs=true leader_registry=true")
