from __future__ import annotations

import csv
import os
import threading
import time
from pathlib import Path

from .config import load_kv_scoped

HEADERS = [
    "observed_at_epoch", "chain_id", "route_id", "token", "kind",
    "expires_at_epoch", "reason",
]

# Deliberately specific. Generic reverts can be transient or caused by RPC/router
# problems and must not permanently condemn a token.
MISMATCH_MARKERS = (
    "INSUFFICIENT_OUTPUT_AMOUNT",
    "TRANSFER_FAILED",
    "TRANSFER_FROM_FAILED",
    "TRANSFERHELPER",
)

_lock = threading.Lock()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _int(v, default: int) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _settings(csv_dir: Path) -> dict:
    try:
        return load_kv_scoped(Path(csv_dir) / "auto_trading_settings.csv", 0)
    except Exception:
        return {}


def _limits(csv_dir: Path) -> tuple[int, int, int, int]:
    cfg = _settings(csv_dir)
    route_seconds = max(30, min(3600, _int(cfg.get("execution_mismatch_route_quarantine_seconds"), 300)))
    strikes_required = max(2, min(10, _int(cfg.get("execution_mismatch_token_strikes"), 3)))
    strike_window = max(60, min(86400, _int(cfg.get("execution_mismatch_strike_window_seconds"), 1800)))
    token_seconds = max(300, min(604800, _int(cfg.get("execution_mismatch_token_quarantine_seconds"), 21600)))
    return route_seconds, strikes_required, strike_window, token_seconds


def is_execution_mismatch(reason: str) -> bool:
    text = str(reason or "").upper()
    return any(marker in text for marker in MISMATCH_MARKERS)


def _atomic_write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows([{h: r.get(h, "") for h in HEADERS} for r in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _intermediate_tokens(path: list[str]) -> list[str]:
    if len(path) <= 2:
        return []
    out = []
    for token in path[1:-1]:
        t = str(token or "").strip().lower()
        if t and t not in out:
            out.append(t)
    return out


def quarantine_state(csv_dir: Path, chain_id, now: int | None = None) -> dict:
    now = int(now or time.time())
    path = Path(csv_dir) / "auto" / "execution_quarantine.csv"
    rows = _rows(path)
    chain = str(chain_id)
    routes, tokens = set(), set()
    for r in rows:
        if str(r.get("chain_id", "")) != chain:
            continue
        try:
            expiry = int(float(r.get("expires_at_epoch") or 0))
        except Exception:
            expiry = 0
        if expiry <= now:
            continue
        kind = str(r.get("kind") or "").upper()
        if kind == "ROUTE_BLOCK" and r.get("route_id"):
            routes.add(str(r["route_id"]))
        elif kind == "TOKEN_BLOCK" and r.get("token"):
            tokens.add(str(r["token"]).lower())
    return {"route_ids": routes, "tokens": tokens}


def route_or_token_blocked(state: dict, route_id: str, path: list[str]) -> tuple[bool, str]:
    if str(route_id) in state.get("route_ids", set()):
        return True, "temporary route execution-mismatch quarantine"
    blocked = state.get("tokens", set())
    for token in _intermediate_tokens(path):
        if token in blocked:
            return True, f"temporary token execution-mismatch quarantine:{token}"
    return False, ""


def record_execution_mismatch(
    csv_dir: Path,
    chain_id,
    route_id: str,
    route_path: list[str] | str,
    reason: str,
    now: int | None = None,
) -> dict:
    """Record a fail-closed V2 execution mismatch.

    One mismatch blocks the exact route briefly.  A token is blocked only after
    repeated specific execution mismatches within the configured strike window.
    This avoids turning a single transient price movement into a long blacklist.
    """
    if not is_execution_mismatch(reason):
        return {"recorded": False, "token_blocks": []}
    now = int(now or time.time())
    route_seconds, strikes_required, strike_window, token_seconds = _limits(Path(csv_dir))
    if isinstance(route_path, str):
        tokens = [x.strip() for x in route_path.split(">") if x.strip()]
    else:
        tokens = [str(x).strip() for x in route_path if str(x).strip()]
    middle = _intermediate_tokens(tokens)
    qpath = Path(csv_dir) / "auto" / "execution_quarantine.csv"
    chain = str(chain_id)

    with _lock:
        rows = _rows(qpath)
        # Keep a bounded recent audit plus any still-active blocks.
        kept = []
        for r in rows:
            try:
                observed = int(float(r.get("observed_at_epoch") or 0))
                expiry = int(float(r.get("expires_at_epoch") or 0))
            except Exception:
                observed, expiry = 0, 0
            if observed >= now - 86400 or expiry > now:
                kept.append(r)
        rows = kept[-5000:]

        rows.append({
            "observed_at_epoch": now, "chain_id": chain, "route_id": str(route_id or ""),
            "token": "", "kind": "ROUTE_BLOCK", "expires_at_epoch": now + route_seconds,
            "reason": str(reason)[:500],
        })

        token_blocks = []
        for token in middle:
            rows.append({
                "observed_at_epoch": now, "chain_id": chain, "route_id": str(route_id or ""),
                "token": token, "kind": "TOKEN_STRIKE", "expires_at_epoch": 0,
                "reason": str(reason)[:500],
            })
            strikes = 0
            for r in rows:
                if str(r.get("chain_id", "")) != chain:
                    continue
                if str(r.get("kind") or "").upper() != "TOKEN_STRIKE":
                    continue
                if str(r.get("token") or "").lower() != token:
                    continue
                try:
                    observed = int(float(r.get("observed_at_epoch") or 0))
                except Exception:
                    observed = 0
                if observed >= now - strike_window:
                    strikes += 1
            if strikes >= strikes_required:
                already = False
                for r in rows:
                    if str(r.get("chain_id", "")) != chain or str(r.get("kind") or "").upper() != "TOKEN_BLOCK":
                        continue
                    if str(r.get("token") or "").lower() != token:
                        continue
                    try:
                        if int(float(r.get("expires_at_epoch") or 0)) > now:
                            already = True
                            break
                    except Exception:
                        pass
                if not already:
                    rows.append({
                        "observed_at_epoch": now, "chain_id": chain, "route_id": "",
                        "token": token, "kind": "TOKEN_BLOCK", "expires_at_epoch": now + token_seconds,
                        "reason": f"{strikes} execution-mismatch strikes in {strike_window}s; latest={str(reason)[:350]}",
                    })
                    token_blocks.append(token)

        _atomic_write(qpath, rows[-5000:])
    return {"recorded": True, "token_blocks": token_blocks}


def backfill_recent_simulation_mismatches(csv_dir: Path, now: int | None = None, lookback_seconds: int = 7200) -> dict:
    """Seed quarantine from recent AUTO simulation audit rows after an upgrade."""
    now = int(now or time.time())
    src = Path(csv_dir) / "auto" / "auto_trade_simulations.csv"
    seen = set()
    recorded = 0
    for r in _rows(src)[-2000:]:
        try:
            ts = int(float(r.get("timestamp_epoch") or 0))
        except Exception:
            ts = 0
        if ts < now - lookback_seconds:
            continue
        reason = str(r.get("reason") or "")
        if not is_execution_mismatch(reason):
            continue
        key = (str(r.get("chain_id") or ""), str(r.get("route_id") or ""), ts, reason[:80])
        if key in seen:
            continue
        seen.add(key)
        record_execution_mismatch(
            csv_dir,
            r.get("chain_id") or "",
            r.get("route_id") or "",
            r.get("route_path") or "",
            reason,
            now=max(ts, now - lookback_seconds),
        )
        recorded += 1
    return {"recorded": recorded}
