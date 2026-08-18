from __future__ import annotations

import csv
import os
import time
from pathlib import Path

MARKER = ".solana_live_position_capacity_2_20260818_applied"


def apply():
    root = Path(__file__).resolve().parent.parent
    path = root / "CSVbot" / "solana_settings.csv"
    marker = root / "data" / MARKER
    if marker.exists():
        print("[solana-capacity] already_applied=true")
        return

    headers = ["setting", "value", "description"]
    rows = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                headers = list(reader.fieldnames)
            rows = list(reader)

    found = False
    changed = False
    for row in rows:
        if str(row.get("setting") or "").strip() == "live_max_positions":
            found = True
            if str(row.get("value") or "").strip() != "2":
                row["value"] = "2"
                changed = True
            if "description" in headers and not str(row.get("description") or "").strip():
                row["description"] = "Maximum simultaneous guarded Solana LIVE positions per Telegram user"
            break

    if not found:
        row = {h: "" for h in headers}
        row["setting"] = "live_max_positions"
        row["value"] = "2"
        if "description" in headers:
            row["description"] = "Maximum simultaneous guarded Solana LIVE positions per Telegram user"
        rows.append(row)
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
    print(f"[solana-capacity] changed={changed} live_max_positions=2")


try:
    apply()
except Exception as exc:
    print(f"[solana-capacity] ERROR {type(exc).__name__}: {exc}")
