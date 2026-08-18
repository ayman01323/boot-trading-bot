from __future__ import annotations

import csv
import os
import time
from pathlib import Path

# Restore a balanced opportunity rate close to the pre-quality-tightening Solana
# behaviour, while leaving LIVE activation, signing, simulation, reserve and
# hard execution protections untouched.
TARGETS = {
    "leaders_per_user": "5",
    "min_closed_trades": "5",
    "min_win_rate_pct": "50",
    "require_complete_history": "false",
    "min_profit_factor": "1.20",
    "recent_trade_window": "10",
    "min_recent_win_rate_pct": "50",
    "min_recent_profit_factor": "1.00",
    "max_leader_drawdown_pct": "30",
    "min_copied_trades_for_guard": "5",
    "min_copied_win_rate_pct": "40",
    "min_copied_profit_factor": "1.0",
    "max_consecutive_copied_losses": "3",
    "leader_suspend_minutes": "180",
    "max_signal_age_seconds": "30",
    "max_roundtrip_loss_pct": "3",
    "max_entry_deterioration_pct": "2",
    "discovery_blocks_per_cycle": "4",
    "discovery_interval_seconds": "10",
    "candidate_limit": "150",
    "history_max_signatures": "400",
    "history_refresh_hours": "8",
    "leader_poll_seconds": "4",
    "position_poll_seconds": "10",
    "stop_loss_pct": "10",
    "take_profit_pct": "25",
    "leader_exit_loss_cap_pct": "2",
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "0.25",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "4",
    "max_hold_hours": "24",
    "mirror_partial_sells": "true",
}

MARKER = ".solana_balanced_frequency_20260818_applied"


def apply():
    root = Path(__file__).resolve().parent.parent
    path = root / "CSVbot" / "solana_settings.csv"
    marker = root / "data" / MARKER
    if marker.exists():
        print("[solana-frequency] already_applied=true")
        return

    headers = ["setting", "value", "description"]
    rows = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                headers = list(reader.fieldnames)
            rows = list(reader)

    by_key = {str(r.get("setting") or "").strip(): r for r in rows}
    changed = False
    for key, value in TARGETS.items():
        row = by_key.get(key)
        if row is None:
            row = {h: "" for h in headers}
            row["setting"] = key
            row["value"] = value
            rows.append(row)
            by_key[key] = row
            changed = True
        elif str(row.get("value") or "").strip() != value:
            row["value"] = value
            changed = True

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for row in rows:
                w.writerow({h: row.get(h, "") for h in headers})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"applied={int(time.time())}\n", encoding="utf-8")
    print(
        "[solana-frequency] changed=%s leaders=5 min_trades=5 min_win=50 pf=1.20 "
        "signal=30 roundtrip=3 entry=2 discovery_blocks=4 candidate_limit=150"
        % changed
    )


try:
    apply()
except Exception as exc:
    print(f"[solana-frequency] ERROR {type(exc).__name__}: {exc}")
