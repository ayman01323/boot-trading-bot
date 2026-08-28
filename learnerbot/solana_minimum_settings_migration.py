from __future__ import annotations

import csv
import os
from pathlib import Path


TARGETS = {
    "live_trade_sol": ("0.009", "Real SOL amount per guarded Solana LIVE copied BUY (fixed at 0.009 in live_limits)"),
    "live_min_sol_reserve": ("0.01", "SOL that must remain untouched for fees and emergency exits"),
}
MARKER_NAME = ".solana_minimum_settings_20260828_fixed009_applied"


def apply() -> None:
    root = Path(__file__).resolve().parent.parent
    path = root / "CSVbot" / "solana_settings.csv"
    marker = root / "data" / MARKER_NAME

    if marker.exists():
        print(
            "[solana-min-settings] already_applied=true "
            f"live_trade_sol={TARGETS['live_trade_sol'][0]} "
            f"live_min_sol_reserve={TARGETS['live_min_sol_reserve'][0]}"
        )
        return

    headers = ["setting", "value", "description"]
    rows: list[dict] = []

    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                headers = list(reader.fieldnames)
            rows = list(reader)

    by_key = {str(r.get("setting") or "").strip(): r for r in rows}
    changed = False
    for key, (value, description) in TARGETS.items():
        row = by_key.get(key)
        if row is None:
            row = {h: "" for h in headers}
            row["setting"] = key
            row["value"] = value
            if "description" in headers:
                row["description"] = description
            rows.append(row)
            by_key[key] = row
            changed = True
        else:
            if str(row.get("value") or "").strip() != value:
                row["value"] = value
                changed = True
            if "description" in headers and not str(row.get("description") or "").strip():
                row["description"] = description

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "live_trade_sol=0.009\nlive_min_sol_reserve=0.01\n",
        encoding="utf-8",
    )

    print(
        "[solana-min-settings] "
        f"live_trade_sol={TARGETS['live_trade_sol'][0]} "
        f"live_min_sol_reserve={TARGETS['live_min_sol_reserve'][0]} "
        f"changed={changed} marker_created=true"
    )


try:
    apply()
except Exception as exc:
    print(f"[solana-min-settings] ERROR {type(exc).__name__}: {exc}")
