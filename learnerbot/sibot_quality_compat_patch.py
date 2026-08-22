from __future__ import annotations

from . import sibot_reasonable_top20_patch as _reasonable


def _quality_compatible_relaxation(app, path):
    """Preserve strict leader-quality floors while relaxing only history completeness.

    The original reasonable-default migration also lowered min_closed_trades and
    min_win_rate_pct.  Those relaxations remain intentionally disabled by the
    quality guard.  The EVM history-complete gate is the single exception: current
    strategy policy requires it to be false so incomplete-but-measured histories can
    still reach the remaining closed-trade/win-rate/PF/drawdown/recent-quality gates.

    This function is idempotent and self-healing.  If an old migration, stale VPS CSV,
    restored backup, or future regression writes the platform wildcard back to true,
    the next normal settings read corrects only that key.
    """
    rows = _reasonable._sibot._rows(path)
    changed = False
    for row in rows:
        key = str(row.get("setting") or "").strip()
        current = str(row.get("value") or "").strip().lower()
        if key == "require_complete_history" and current == "true":
            row["value"] = "false"
            changed = True
    if changed:
        _reasonable._sibot._atomic_csv(
            path,
            rows,
            ["chain_id", "setting", "value", "description"],
        )


# Keep the profit-first Top-20 wrapper in the chain, but replace its old broad
# relaxation with the single evidence-backed history-complete correction above.
_reasonable._migrate_reasonable_defaults = _quality_compatible_relaxation
