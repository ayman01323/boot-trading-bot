from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from statistics import median

import requests

from learnerbot.config import AppSettings, load_chains

WINDOW_SECONDS = 7 * 24 * 60 * 60
CURRENT_SECONDS = 24 * 60 * 60
MAX_EVM_EVENTS = 2500
MAX_RPC_FALLBACKS_PER_CHAIN = 60
RPC_PROBE_SAMPLES = 3


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], percentile: float):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = (len(ordered) - 1) * float(percentile)
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return round(ordered[lo] * (1.0 - frac) + ordered[hi] * frac, 2)


def _stats(values: list[float]) -> dict:
    clean = [float(v) for v in values if v is not None and float(v) >= 0]
    return {
        "count": len(clean),
        "p50_ms": _percentile(clean, 0.50),
        "p95_ms": _percentile(clean, 0.95),
        "max_ms": round(max(clean), 2) if clean else None,
    }


def _trade_ref(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _read_only_sqlite(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)


def _json_rpc(url: str, method: str, params: list, *, timeout: float = 8.0):
    started = time.perf_counter()
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload.get("error"))[:300])
    return payload.get("result"), elapsed_ms


def _rpc_probe(urls: list[str], method: str, params: list) -> dict:
    errors = 0
    for endpoint_index, url in enumerate(urls):
        samples = []
        try:
            for _ in range(RPC_PROBE_SAMPLES):
                _, elapsed_ms = _json_rpc(url, method, params)
                samples.append(elapsed_ms)
            return {
                "available": True,
                "endpoint_index": endpoint_index,
                "samples": len(samples),
                **_stats(samples),
            }
        except Exception:
            errors += 1
    return {
        "available": False,
        "endpoint_index": None,
        "samples": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
        "failed_endpoints": errors,
    }


def _load_evm_broadcasts(app: AppSettings, cutoff: int) -> list[dict]:
    path = Path(app.csv_dir) / "auto" / "trade_provenance.sqlite3"
    if not path.exists():
        return []
    try:
        conn = _read_only_sqlite(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT MIN(event_ts) AS event_ts,
                   COALESCE(chain_id,'') AS chain_id,
                   COALESCE(chain_slug,'') AS chain_slug,
                   LOWER(tx_hash) AS tx_hash
              FROM trade_events
             WHERE event_ts >= ?
               AND TRIM(COALESCE(tx_hash,'')) <> ''
               AND LOWER(COALESCE(chain_slug,'')) <> 'solana'
             GROUP BY COALESCE(chain_id,''), COALESCE(chain_slug,''), LOWER(tx_hash)
             ORDER BY event_ts DESC
             LIMIT ?
            """,
            (int(cutoff), MAX_EVM_EVENTS),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _local_evm_block_time(app: AppSettings, chain_slug: str, tx_hash: str):
    path = Path(app.data_dir) / f"{chain_slug}.sqlite3"
    if not path.exists():
        return None
    try:
        conn = _read_only_sqlite(path)
        row = conn.execute(
            """
            SELECT b.timestamp
              FROM transactions t
              JOIN blocks b ON b.number=t.block_number
             WHERE LOWER(t.tx_hash)=LOWER(?)
             LIMIT 1
            """,
            (str(tx_hash),),
        ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _rpc_evm_block_time(urls: list[str], tx_hash: str, block_cache: dict):
    for endpoint_index, url in enumerate(urls):
        try:
            receipt, _ = _json_rpc(url, "eth_getTransactionReceipt", [tx_hash])
            if not receipt or not receipt.get("blockNumber"):
                continue
            block_hex = str(receipt["blockNumber"])
            cache_key = (endpoint_index, block_hex)
            if cache_key not in block_cache:
                block, _ = _json_rpc(url, "eth_getBlockByNumber", [block_hex, False])
                if not block or not block.get("timestamp"):
                    continue
                block_cache[cache_key] = int(str(block["timestamp"]), 16)
            return int(block_cache[cache_key]), endpoint_index
        except Exception:
            continue
    return None, None


def _baseline_comparison(samples: list[dict], now: int) -> dict:
    current_cutoff = int(now) - CURRENT_SECONDS
    current = [float(r["latency_ms"]) for r in samples if int(r["event_epoch"]) >= current_cutoff]
    historical = [float(r["latency_ms"]) for r in samples if int(r["event_epoch"]) < current_cutoff]
    current_stats = _stats(current)
    historical_stats = _stats(historical)
    p50_delta = None
    p95_delta = None
    if current_stats["p50_ms"] is not None and historical_stats["p50_ms"] not in (None, 0):
        p50_delta = round((current_stats["p50_ms"] / historical_stats["p50_ms"] - 1.0) * 100.0, 2)
    if current_stats["p95_ms"] is not None and historical_stats["p95_ms"] not in (None, 0):
        p95_delta = round((current_stats["p95_ms"] / historical_stats["p95_ms"] - 1.0) * 100.0, 2)
    return {
        "current_24h": current_stats,
        "normal_baseline": {
            "definition": "same-server measured trades from the preceding six days",
            **historical_stats,
        },
        "p50_delta_pct": p50_delta,
        "p95_delta_pct": p95_delta,
        "baseline_sufficient": historical_stats["count"] >= 5,
    }


def _evm_snapshot(app: AppSettings, now: int) -> list[dict]:
    cutoff = int(now) - WINDOW_SECONDS
    configs = load_chains(app, enabled_only=False)
    by_id = {str(c.chain_id): c for c in configs if str(c.type).upper() == "EVM"}
    by_slug = {str(c.slug).lower(): c for c in configs if str(c.type).upper() == "EVM"}
    events = _load_evm_broadcasts(app, cutoff)
    grouped: dict[str, list[dict]] = defaultdict(list)
    total_by_chain: dict[str, int] = defaultdict(int)
    rpc_fallbacks: dict[str, int] = defaultdict(int)
    block_cache: dict = {}

    for event in events:
        chain = by_id.get(str(event.get("chain_id") or "")) or by_slug.get(str(event.get("chain_slug") or "").lower())
        if chain is None:
            continue
        slug = str(chain.slug).lower()
        total_by_chain[slug] += 1
        tx_hash = str(event.get("tx_hash") or "").strip()
        event_epoch = int(event.get("event_ts") or 0)
        if not tx_hash or not event_epoch:
            continue
        block_epoch = _local_evm_block_time(app, slug, tx_hash)
        source = "local_chain_index" if block_epoch is not None else ""
        endpoint_index = None
        if block_epoch is None and rpc_fallbacks[slug] < MAX_RPC_FALLBACKS_PER_CHAIN:
            rpc_fallbacks[slug] += 1
            block_epoch, endpoint_index = _rpc_evm_block_time(list(chain.rpc_urls), tx_hash, block_cache)
            source = "configured_rpc" if block_epoch is not None else ""
        if block_epoch is None:
            continue
        latency_ms = max(0.0, float(block_epoch - event_epoch) * 1000.0)
        grouped[slug].append(
            {
                "trade_ref": _trade_ref(tx_hash),
                "event_epoch": event_epoch,
                "age_hours": round(max(0, now - event_epoch) / 3600.0, 2),
                "latency_ms": round(latency_ms, 2),
                "measurement_source": source,
                "rpc_endpoint_index": endpoint_index,
            }
        )

    out = []
    for chain in configs:
        if str(chain.type).upper() != "EVM" or not chain.enabled:
            continue
        slug = str(chain.slug).lower()
        samples = sorted(grouped.get(slug, []), key=lambda row: row["event_epoch"], reverse=True)
        comparison = _baseline_comparison(samples, now)
        out.append(
            {
                "chain_slug": slug,
                "chain_id": int(chain.chain_id),
                "chain_name": str(chain.name),
                "metric": "broadcast_to_block_inclusion_ms",
                "metric_scope": "local broadcast audit timestamp to mined block timestamp; second-resolution inclusion metric",
                "trades_7d": int(total_by_chain.get(slug, 0)),
                "measured_trades_7d": len(samples),
                "measurement_coverage_pct": round((len(samples) / total_by_chain[slug] * 100.0), 2) if total_by_chain.get(slug) else None,
                "rpc_round_trip": _rpc_probe(list(chain.rpc_urls), "eth_blockNumber", []),
                **comparison,
                "per_trade": [
                    {k: v for k, v in row.items() if k != "event_epoch"}
                    for row in samples[:200]
                ],
                "per_trade_truncated": len(samples) > 200,
            }
        )
    return out


def _solana_rpc_urls(app: AppSettings) -> list[str]:
    env_url = str(os.getenv("SOLANA_RPC_URL") or "").strip()
    if env_url:
        return [env_url]
    path = Path(app.csv_dir) / "solana_settings.csv"
    for row in _read_rows(path):
        if str(row.get("setting") or "").strip() == "rpc_url":
            value = str(row.get("value") or "").strip()
            if value:
                return [value]
    return ["https://api.mainnet-beta.solana.com"]


def _solana_snapshot(app: AppSettings, now: int):
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    cutoff = int(now) - WINDOW_SECONDS
    samples = []
    trades_7d = 0
    if path.exists():
        try:
            conn = _read_only_sqlite(path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT p.position_id, p.entry_ts, e.event_ts
                  FROM positions p
                  LEFT JOIN leader_events e ON e.signature=p.leader_buy_signature
                 WHERE p.mode='LIVE' AND p.entry_ts>=?
                 ORDER BY p.entry_ts DESC
                 LIMIT 2500
                """,
                (cutoff,),
            ).fetchall()
            conn.close()
            trades_7d = len(rows)
            for raw in rows:
                row = dict(raw)
                entry_ts = int(row.get("entry_ts") or 0)
                event_ts = int(row.get("event_ts") or 0)
                if not entry_ts or not event_ts or entry_ts < event_ts:
                    continue
                samples.append(
                    {
                        "trade_ref": _trade_ref(row.get("position_id") or ""),
                        "event_epoch": entry_ts,
                        "age_hours": round(max(0, now - entry_ts) / 3600.0, 2),
                        "latency_ms": round(float(entry_ts - event_ts) * 1000.0, 2),
                        "measurement_source": "solana_leader_event_and_live_position",
                        "rpc_endpoint_index": None,
                    }
                )
        except Exception:
            samples = []
            trades_7d = 0
    samples.sort(key=lambda row: row["event_epoch"], reverse=True)
    comparison = _baseline_comparison(samples, now)
    return {
        "chain_slug": "solana",
        "chain_id": -101,
        "chain_name": "Solana",
        "metric": "leader_signal_to_copy_entry_ms",
        "metric_scope": "observed leader-event timestamp to local LIVE copied-position entry timestamp; this is not validator confirmation latency",
        "trades_7d": trades_7d,
        "measured_trades_7d": len(samples),
        "measurement_coverage_pct": round((len(samples) / trades_7d * 100.0), 2) if trades_7d else None,
        "rpc_round_trip": _rpc_probe(_solana_rpc_urls(app), "getSlot", [{"commitment": "processed"}]),
        **comparison,
        "per_trade": [
            {k: v for k, v in row.items() if k != "event_epoch"}
            for row in samples[:200]
        ],
        "per_trade_truncated": len(samples) > 200,
    }


def _server_economics() -> dict:
    provider = str(os.getenv("ENGINEERING_CURRENT_SERVER_PROVIDER") or "").strip()
    region = str(os.getenv("ENGINEERING_CURRENT_SERVER_REGION") or "").strip()
    currency = str(os.getenv("ENGINEERING_CURRENT_SERVER_CURRENCY") or "USD").strip().upper() or "USD"
    amount = _safe_float(os.getenv("ENGINEERING_CURRENT_SERVER_MONTHLY_COST"))
    known = bool(provider or region or amount is not None)
    return {
        "current_server": {
            "known": known,
            "provider": provider or None,
            "region": region or None,
            "monthly_cost": {"amount": amount, "currency": currency} if amount is not None else None,
            "source": "repository variables supplied to the self-hosted snapshot" if known else "not_configured",
        },
        "candidate_prices_in_snapshot": False,
        "comparison_rule": (
            "Engineering must not recommend migration from latency alone. Compare measured chain-weighted latency, "
            "trade frequency and execution outcome evidence against current and alternative monthly cost. "
            "Any alternative price must identify a source/date or be marked unverified."
        ),
    }


def build_snapshot(app: AppSettings, *, now: int | None = None) -> dict:
    current = int(now or time.time())
    chains = _evm_snapshot(app, current)
    solana = _solana_snapshot(app, current)
    if solana["trades_7d"] or solana["rpc_round_trip"].get("available"):
        chains.append(solana)
    total_trades = sum(int(row.get("trades_7d") or 0) for row in chains)
    for row in chains:
        row["trade_share_pct_7d"] = round((int(row.get("trades_7d") or 0) / total_trades * 100.0), 2) if total_trades else None
    return {
        "schema_version": 1,
        "generated_epoch": current,
        "trade_latency": {
            "required_engineering_review": True,
            "window": "7d with current 24h compared against the preceding six-day same-server baseline",
            "normal_definition": "historical same-server baseline, never an invented protocol-wide number",
            "chains": chains,
            "total_trades_7d": total_trades,
            "report_requirements": [
                "Report every blockchain with observed trades separately.",
                "Show per-trade measured latency when available, plus 24h p50/p95 versus the preceding six-day baseline.",
                "Keep RPC round-trip latency separate from transaction inclusion/copy latency.",
                "If telemetry is missing or baseline count is below five, say INSUFFICIENT DATA rather than estimating a measured result.",
                "Assess trading impact only where success/failure, slippage or PnL evidence supports the inference.",
                "Rank infrastructure recommendations by actual seven-day chain trade share and latency degradation.",
            ],
            "measurement_limits": [
                "EVM inclusion timestamps are second-resolution and do not measure sub-second local signing time.",
                "Solana currently measures leader-signal-to-copy-entry latency, not validator confirmation latency.",
                "Only configured RPC endpoints are probed and endpoint URLs/credentials are never published.",
            ],
        },
        "infrastructure": _server_economics(),
        "privacy": {
            "no_wallet_addresses": True,
            "no_raw_transaction_hashes": True,
            "trade_references_are_one_way_hash_prefixes": True,
            "no_rpc_urls_or_credentials": True,
            "no_private_keys": True,
            "no_process_environment_dump": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--now", type=int)
    args = parser.parse_args()
    app = AppSettings.load()
    payload = build_snapshot(app, now=args.now)
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "chains": len(payload["trade_latency"]["chains"]),
        "trades_7d": payload["trade_latency"]["total_trades_7d"],
        "server_cost_known": payload["infrastructure"]["current_server"]["monthly_cost"] is not None,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
