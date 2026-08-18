from __future__ import annotations

import csv
import os
import time
from pathlib import Path

TARGETS = {
    "leaders_per_user": "3",
    "min_closed_trades": "10",
    "min_win_rate_pct": "55",
    "require_complete_history": "false",
    "min_profit_factor": "1.5",
    "recent_trade_window": "20",
    "min_recent_win_rate_pct": "55",
    "min_recent_profit_factor": "1.10",
    "max_leader_drawdown_pct": "20",
    "min_copied_trades_for_guard": "3",
    "min_copied_win_rate_pct": "40",
    "min_copied_profit_factor": "1.0",
    "max_consecutive_copied_losses": "3",
    "leader_suspend_minutes": "360",
    "max_signal_age_seconds": "20",
    "max_roundtrip_loss_pct": "2",
    "max_entry_deterioration_pct": "1.5",
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
MARKER = ".solana_quality_settings_20260818_applied"


def apply():
    root = Path(__file__).resolve().parent.parent
    path = root / "CSVbot" / "solana_settings.csv"
    marker = root / "data" / MARKER
    if marker.exists():
        print("[solana-quality] already_applied=true")
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
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"applied={int(time.time())}\n", encoding="utf-8")
    print("[solana-quality] changed=%s leaders=3 min_trades=10 min_win=55 pf=1.5 max_dd=20 roundtrip=2 entry=1.5" % changed)


try:
    apply()
except Exception as exc:
    print(f"[solana-quality] ERROR {type(exc).__name__}: {exc}")
