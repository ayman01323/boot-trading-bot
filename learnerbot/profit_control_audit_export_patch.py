from __future__ import annotations

from . import transaction_audit as _audit

_parts = list(_audit.RELEVANT_TABLE_PARTS)
for _part in ("strategy", "control"):
    if _part not in _parts:
        _parts.append(_part)
_audit.RELEVANT_TABLE_PARTS = tuple(_parts)

# Import after the existing transaction-audit enrichment so the Strategy Laboratory
# wraps the current loss-forensics builder and adds separate per-strategy scorecards to
# the same sanitised hourly report consumed by the AI review lane.  This is reporting
# only: it cannot arm LIVE trading or bypass execution controls.
from . import strategy_lab_audit_patch as _strategy_lab_audit  # noqa: E402,F401

print("[profit-control-audit] strategy_registry=true control_runs=true leader_registry=true strategy_lab=true")
