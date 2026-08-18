from __future__ import annotations

import csv
import os
import time
from pathlib import Path

# Operational latency tuning only. The LIVE risk gates are deliberately not
# changed: signals still have to arrive within the existing 30-second freshness
# window and pass entry-deterioration, round-trip, simulation and reserve checks.
TARGETS = {
    "leader_poll_seconds": "2",
    "rpc_delay_seconds": "0.50",
}

MARKER = ".solana_live_latency_priority_20260818_v1"


def apply():
    root = Path(__file__).resolve().parent.parent
    path = root / "CSVbot" / "solana_settings.csv"
    marker = root / "data" / MARKER
    if marker.exists():
        print("[solana-live-latency] already_applied=true")
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
    descriptions = {
        "leader_poll_seconds": "Fresh selected-leader signature polling cadence; LIVE latency priority",
        "rpc_delay_seconds": "Delay between heavy historical Solana transaction RPC calls; protects LIVE RPC capacity",
    }
    changed = False
    for key, value in TARGETS.items():
        row = by_key.get(key)
        if row is None:
            row = {h: "" for h in headers}
            row["setting"] = key
            row["value"] = value
            if "description" in headers:
                row["description"] = descriptions[key]
            rows.append(row)
            by_key[key] = row
            changed = True
        else:
            if str(row.get("value") or "").strip() != value:
                row["value"] = value
                changed = True
            if "description" in headers:
                row["description"] = descriptions[key]

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
        "\n".join([
            f"applied_epoch={int(time.time())}",
            "leader_poll_seconds=2",
            "rpc_delay_seconds=0.50",
            "max_signal_age_seconds_unchanged=true",
            "entry_quality_gates_unchanged=true",
            "live_safety_gates_unchanged=true",
        ]) + "\n",
        encoding="utf-8",
    )
    print(
        "[solana-live-latency] leader_poll=2s history_rpc_delay=0.50s "
        "freshness_and_risk_gates_unchanged=true changed=%s" % changed
    )


try:
    apply()
except Exception as exc:
    print(f"[solana-live-latency] ERROR {type(exc).__name__}: {exc}")
