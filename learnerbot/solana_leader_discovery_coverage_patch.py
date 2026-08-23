from __future__ import annotations

"""Broaden Solana leader discovery while leaving every trading gate unchanged.

The original discovery worker inspected only two sequential finalized blocks every
15 seconds.  Solana advances far faster than that, so the worker periodically
jumped forward and sampled only a very small slice of fresh activity.  It also
required swap-like log text and therefore missed Jupiter routed instructions whose
log name is `Route`/`SharedAccountsRoute` rather than `Swap`.

This patch changes discovery only:
- sample a bounded, rotating cross-section of recent finalized blocks;
- recognise routed Jupiter/Raydium-style swap instruction names, while still
  requiring classify_swap() to prove a one-token SOL-denominated balance change;
- allow history backfill to consider up to 300 active candidates instead of 100;
- publish read-only coverage telemetry.

It does not change leader-quality, win-rate, PF, drawdown, median-return,
liquidity, simulation, preflight, signing, LIVE/ARMED, capital, stop or exit gates.
"""

import json
import os
import threading
import time
from contextlib import closing
from pathlib import Path

from . import solana_sibot as _sol

_PREV_LOOKS_LIKE_SWAP = _sol._looks_like_swap
_BRIDGE = Path("/var/tmp/boot/solana_discovery_coverage.json")
_BRIDGE_LOCK = threading.Lock()

_ROUTE_LOG_MARKERS = (
    "instruction: route",
    "instruction: sharedaccountsroute",
    "instruction: exactoutroute",
    "instruction: swapbasein",
    "instruction: swapbaseout",
    "instruction: twohopswap",
)


def looks_like_swap(result: dict) -> bool:
    if _PREV_LOOKS_LIKE_SWAP(result):
        return True
    logs = ((result.get("meta") or {}).get("logMessages") or [])
    text = "\n".join(str(x).lower().replace(" ", "") for x in logs)
    # Normalise away spaces because providers/program versions vary between
    # `SharedAccountsRoute` and `Shared Accounts Route` style log rendering.
    return any(marker.replace(" ", "") in text for marker in _ROUTE_LOG_MARKERS)


def _atomic_bridge(payload: dict) -> None:
    try:
        with _BRIDGE_LOCK:
            _BRIDGE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _BRIDGE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, _BRIDGE)
    except Exception:
        pass


def _sample_slots(latest: int, count: int, window: int, phase: int) -> list[int]:
    latest = max(0, int(latest))
    count = max(1, min(10, int(count)))
    window = max(count, min(256, int(window)))
    start = max(0, latest - window + 1)
    width = latest - start + 1
    if width <= count:
        return list(range(start, latest + 1))

    stride = max(1, width // count)
    offset = int(phase) % stride
    slots = []
    value = start + offset
    while value <= latest and len(slots) < count:
        slots.append(value)
        value += stride
    if latest not in slots:
        if len(slots) >= count:
            slots[-1] = latest
        else:
            slots.append(latest)
    return sorted(set(slots))


def discover_recent_blocks(app) -> int:
    cfg = _sol.settings(app)
    if not _sol._bool(cfg.get("enabled"), True):
        return 0

    latest = int(_sol._rpc(app, "getSlot", [{"commitment": "finalized"}]) or 0)
    sampled = max(1, min(10, _sol._int(cfg.get("discovery_sampled_blocks_per_cycle"), 8)))
    window = max(sampled, min(256, _sol._int(cfg.get("discovery_recent_window_slots"), 48)))

    with closing(_sol.connect(app)) as conn:
        phase = _sol._int(_sol._state(conn, "discovery_sample_phase", 0), 0)
    slots = _sample_slots(latest, sampled, window, phase)

    found = 0
    blocks_ok = 0
    tx_seen = 0
    swap_like = 0
    unique_wallets = set()

    for slot in slots:
        try:
            block = _sol._rpc(
                app,
                "getBlock",
                [
                    int(slot),
                    {
                        "commitment": "finalized",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "rewards": False,
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
        except Exception:
            block = None
        if not block:
            continue

        blocks_ok += 1
        block_time = int(block.get("blockTime") or time.time())
        for item in block.get("transactions") or []:
            tx_seen += 1
            result = {
                "slot": int(slot),
                "blockTime": block_time,
                "transaction": item.get("transaction") or {},
                "meta": item.get("meta") or {},
            }
            if result["meta"].get("err") is not None or not _sol._looks_like_swap(result):
                continue
            swap_like += 1
            for wallet in _sol._signers(result)[:2]:
                event = _sol.classify_swap(result, wallet)
                if not event:
                    continue
                now = int(time.time())
                with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
                    conn.execute(
                        """INSERT INTO candidates(wallet,first_seen,last_seen,swap_events,last_signature,updated_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(wallet) DO UPDATE SET last_seen=MAX(candidates.last_seen,excluded.last_seen),
                             swap_events=candidates.swap_events+1,last_signature=excluded.last_signature,updated_at=excluded.updated_at""",
                        (wallet, event["event_ts"], event["event_ts"], 1, event["signature"], now),
                    )
                    conn.commit()
                unique_wallets.add(str(wallet))
                found += 1

    with closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, "last_discovery_slot", latest)
        _sol._set_state(conn, "discovery_sample_phase", phase + 1)

    _atomic_bridge(
        {
            "schema_version": 1,
            "generated_epoch": int(time.time()),
            "latest_finalized_slot": latest,
            "recent_window_slots": window,
            "sampled_slots": slots,
            "sampled_blocks_requested": len(slots),
            "sampled_blocks_ok": blocks_ok,
            "transactions_seen": tx_seen,
            "swap_like_transactions": swap_like,
            "candidate_events_found": found,
            "unique_candidate_wallets_found": len(unique_wallets),
            "route_log_extensions": True,
            "history_candidate_limit": max(20, min(1000, _sol._int(cfg.get("history_candidate_limit"), 300))),
            "quality_thresholds_unchanged": True,
            "live_execution_unchanged": True,
        }
    )
    return found


def next_history_wallet(app):
    cfg = _sol.settings(app)
    limit = max(20, min(1000, _sol._int(cfg.get("history_candidate_limit"), 300)))
    refresh_after = max(1, _sol._int(cfg.get("history_refresh_hours"), 12)) * 3600
    now = int(time.time())
    with closing(_sol.connect(app)) as conn:
        candidates = [
            str(r["wallet"])
            for r in conn.execute(
                "SELECT wallet FROM candidates ORDER BY swap_events DESC,last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        fetched = {
            str(r["wallet"]): int(r["fetched_at"] or 0)
            for r in conn.execute("SELECT wallet,fetched_at FROM history_status").fetchall()
        }
        leaders = [str(r["wallet"]) for r in conn.execute("SELECT DISTINCT wallet FROM leaders").fetchall()]

    ordered = []
    for wallet in leaders + candidates:
        if wallet not in ordered:
            ordered.append(wallet)
    ordered.sort(key=lambda wallet: (0 if wallet in _sol._REFRESH_NOW else 1, fetched.get(wallet, 0)))
    for wallet in ordered:
        if wallet in _sol._REFRESH_NOW or now - fetched.get(wallet, 0) >= refresh_after:
            _sol._REFRESH_NOW.discard(wallet)
            return wallet
    return None


def install() -> None:
    if getattr(_sol, "_leader_discovery_coverage_installed", False):
        return

    _sol.DEFAULTS.setdefault(
        "discovery_sampled_blocks_per_cycle",
        ("8", "Bounded rotating sample of recent finalized Solana blocks per discovery cycle"),
    )
    _sol.DEFAULTS.setdefault(
        "discovery_recent_window_slots",
        ("48", "Recent finalized-slot window used for rotating Solana discovery sampling"),
    )
    _sol.DEFAULTS.setdefault(
        "history_candidate_limit",
        ("300", "Active discovered Solana wallets eligible for bounded history backfill"),
    )

    _sol._looks_like_swap = looks_like_swap
    _sol.discover_recent_blocks = discover_recent_blocks
    _sol._next_history_wallet = next_history_wallet
    _sol._leader_discovery_coverage_installed = True
    print(
        "[solana-leader-discovery] sampled_recent_blocks=8 window=48 route_logs=extended "
        "history_candidates=300 quality_gates=unchanged live_execution=unchanged",
        flush=True,
    )


install()
