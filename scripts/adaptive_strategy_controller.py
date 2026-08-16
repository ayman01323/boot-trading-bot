#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

from learnerbot.config import AppSettings
from learnerbot.telegram import send_to_chats

# This controller is deliberately restricted to discovery/search breadth.
# It MUST NOT alter capital, slippage, minimum-profit, gas bidding, signing,
# private-key handling, or final simulation/eth_call safety gates.
ALLOWED_SETTINGS = {
    "fast_market_interval_seconds",
    "fast_market_max_candidate_checks",
    "product_max_scan_tokens_per_chain",
    "product_new_token_shadow_seconds",
    "product_established_age_seconds",
    "product_established_min_pools",
    "product_strict_min_pools",
    "max_auto_trades_per_hour",
    "cooldown_seconds",
}

PROFILES = [
    (
        "CORE_LIQUIDITY",
        {
            "fast_market_interval_seconds": "5",
            "fast_market_max_candidate_checks": "60",
            "product_max_scan_tokens_per_chain": "60",
            "product_new_token_shadow_seconds": "300",
            "product_established_age_seconds": "900",
            "product_established_min_pools": "2",
            "product_strict_min_pools": "2",
            "max_auto_trades_per_hour": "12",
            "cooldown_seconds": "5",
        },
    ),
    (
        "BREADTH",
        {
            "fast_market_interval_seconds": "5",
            "fast_market_max_candidate_checks": "70",
            "product_max_scan_tokens_per_chain": "80",
            "product_new_token_shadow_seconds": "240",
            "product_established_age_seconds": "600",
            "product_established_min_pools": "2",
            "product_strict_min_pools": "2",
            "max_auto_trades_per_hour": "12",
            "cooldown_seconds": "5",
        },
    ),
    (
        "DIVERSIFY",
        {
            "fast_market_interval_seconds": "5",
            "fast_market_max_candidate_checks": "80",
            "product_max_scan_tokens_per_chain": "100",
            "product_new_token_shadow_seconds": "180",
            "product_established_age_seconds": "420",
            "product_established_min_pools": "2",
            "product_strict_min_pools": "2",
            "max_auto_trades_per_hour": "12",
            "cooldown_seconds": "5",
        },
    ),
    (
        "MAX_SAFE_SEARCH",
        {
            "fast_market_interval_seconds": "5",
            "fast_market_max_candidate_checks": "90",
            "product_max_scan_tokens_per_chain": "120",
            "product_new_token_shadow_seconds": "120",
            "product_established_age_seconds": "300",
            "product_established_min_pools": "2",
            "product_strict_min_pools": "2",
            "max_auto_trades_per_hour": "12",
            "cooldown_seconds": "5",
        },
    ),
]

SUCCESS = {"SUCCESS", "SUCCESS_FEE_PENDING"}
FINAL_STATES = {"TARGET_ACHIEVED", "DEADLINE", "STOPPED"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _epoch(row: dict) -> int:
    try:
        return int(float(row.get("timestamp_epoch") or 0))
    except Exception:
        return 0


def _bool(v) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _chat_ids(app: AppSettings) -> list[str]:
    ids = [str(x).strip() for x in (app.telegram_chat_ids or []) if str(x).strip()]
    for row in _rows(Path(app.csv_dir) / "users.csv"):
        if str(row.get("status") or "").upper() == "ACTIVE":
            tid = str(row.get("telegram_id") or "").strip()
            if tid:
                ids.append(tid)
    return list(dict.fromkeys(ids))


def _send(app: AppSettings, text: str) -> None:
    ids = _chat_ids(app)
    if not app.telegram_bot_token or not ids:
        return
    try:
        send_to_chats(app.telegram_bot_token, ids, text)
    except Exception:
        pass


def _state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _write_settings(path: Path, values: dict[str, str]) -> None:
    unknown = set(values) - ALLOWED_SETTINGS
    if unknown:
        raise RuntimeError(f"refusing non-whitelisted challenge settings: {sorted(unknown)}")

    fields = ["chain_id", "setting", "value", "description"]
    rows: list[dict] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or fields)
            rows = list(reader)
    for field in ("chain_id", "setting", "value", "description"):
        if field not in fields:
            fields.append(field)

    seen: set[str] = set()
    for row in rows:
        cid = str(row.get("chain_id") or "").strip()
        key = str(row.get("setting") or "").strip()
        if cid == "*" and key in values:
            row["value"] = str(values[key])
            row["description"] = "Adaptive challenge search tuning; economic/safety gates unchanged"
            seen.add(key)

    for key, value in values.items():
        if key in seen:
            continue
        row = {field: "" for field in fields}
        row.update(
            {
                "chain_id": "*",
                "setting": key,
                "value": str(value),
                "description": "Adaptive challenge search tuning; economic/safety gates unchanged",
            }
        )
        rows.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".adaptive.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{k: row.get(k, "") for k in fields} for row in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _telemetry(csv_dir: Path, since: int) -> dict:
    sims = [r for r in _rows(csv_dir / "auto" / "auto_trade_simulations.csv") if _epoch(r) >= since]
    executions = [r for r in _rows(csv_dir / "auto" / "auto_trade_execution.csv") if _epoch(r) >= since]
    passed = sum(_bool(r.get("simulation_ok")) for r in sims)
    wins = sum(str(r.get("status") or "").upper() in SUCCESS for r in executions)

    opps = _rows(csv_dir / "auto" / "live_opportunities.csv")
    if not opps:
        opps = _rows(csv_dir / "auto" / "full_power_opportunities.csv")
    enabled = sum(_bool(r.get("enabled")) for r in opps)

    return {
        "simulations": len(sims),
        "sim_passed": passed,
        "successes": wins,
        "opportunities": len(opps),
        "enabled": enabled,
    }


def _profile_index(elapsed: int, t: dict) -> int:
    # Time-based escalation, accelerated only by lack of usable simulations.
    if elapsed >= 90 * 60:
        idx = 3
    elif elapsed >= 45 * 60:
        idx = 2
    elif elapsed >= 20 * 60:
        idx = 1
    else:
        idx = 0

    # If there are no simulation passes in a meaningful sample, broaden discovery
    # one level. We never weaken slippage/profit/final-call checks to manufacture a trade.
    if t["simulations"] >= 10 and t["sim_passed"] == 0:
        idx = min(3, idx + 1)
    if t["opportunities"] == 0 and elapsed >= 10 * 60:
        idx = min(3, idx + 1)

    # After a confirmed success, avoid needless further escalation for 15 minutes.
    if t["successes"] > 0:
        idx = max(0, idx - 1)
    return idx


def main() -> int:
    app = AppSettings.load()
    csv_dir = Path(app.csv_dir)
    settings = csv_dir / "auto_trading_settings.csv"
    status_path = csv_dir / "auto" / "profit_challenge_status.json"
    start_wait = int(time.time())
    last_profile = None
    last_notice = 0

    # Wait for the bounded challenge to publish RUNNING state.
    while int(time.time()) - start_wait < 15 * 60:
        st = _state(status_path)
        if str(st.get("status") or "").upper() == "RUNNING":
            break
        if str(st.get("status") or "").upper() in FINAL_STATES:
            return 0
        time.sleep(20)
    else:
        return 2

    challenge_start = int(_state(status_path).get("start_epoch") or time.time())
    _send(
        app,
        "🧠 BOOT ADAPTIVE MULTI-STRATEGY CONTROLLER ON\n"
        "Parallel engines remain active: direct V2/V3 market routes, learned-wallet route discovery, copy/behaviour research, dynamic product discovery and cross-DEX shadow learning.\n"
        "Controller adapts search breadth only. Capital, slippage, minimum-profit, gas and final simulation/eth_call safety are unchanged.",
    )

    while True:
        now = int(time.time())
        st = _state(status_path)
        status = str(st.get("status") or "").upper()
        if status in FINAL_STATES:
            _send(app, f"🧠 BOOT adaptive strategy controller stopped: challenge status {status}.")
            return 0

        # Use a rolling 15-minute window to judge whether the current search is productive.
        t = _telemetry(csv_dir, now - 15 * 60)
        elapsed = max(0, now - challenge_start)
        idx = _profile_index(elapsed, t)
        name, values = PROFILES[idx]

        if name != last_profile:
            _write_settings(settings, values)
            last_profile = name
            _send(
                app,
                "🧠 BOOT adaptive strategy profile → " + name + "\n"
                f"15m telemetry: opportunities={t['opportunities']} enabled={t['enabled']} simulations={t['simulations']} passed={t['sim_passed']} confirmed_successes={t['successes']}\n"
                "Action: broaden/retune discovery only; no economic or execution safety gate relaxed.",
            )

        # Sparse heartbeat: the main challenge already sends regular P&L reports.
        if now - last_notice >= 30 * 60:
            _send(
                app,
                f"🧠 BOOT strategy controller active — {name}\n"
                f"15m: opportunities={t['opportunities']}, sims={t['simulations']}, passed={t['sim_passed']}, confirmed={t['successes']}.",
            )
            last_notice = now

        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
