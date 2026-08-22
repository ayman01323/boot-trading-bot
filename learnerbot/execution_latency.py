from __future__ import annotations

import sqlite3
import time
from pathlib import Path

WINDOW_SECONDS = 7 * 24 * 60 * 60
CURRENT_SECONDS = 24 * 60 * 60

_STAGE_COLUMNS = (
    "receive_delay_ms",
    "strategy_ms",
    "pre_balance_ms",
    "order_ms",
    "transaction_construction_ms",
    "simulation_ms",
    "execute_ms",
    "post_balance_ms",
    "execution_total_ms",
    "total_event_to_result_ms",
)

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS execution_latency_samples(
  attempt_key TEXT PRIMARY KEY,
  recorded_at_ms INTEGER NOT NULL,
  telegram_id TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  receive_delay_ms REAL,
  strategy_ms REAL,
  pre_balance_ms REAL,
  order_ms REAL,
  transaction_construction_ms REAL,
  simulation_ms REAL,
  execute_ms REAL,
  post_balance_ms REAL,
  execution_total_ms REAL,
  total_event_to_result_ms REAL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_latency_time
  ON execution_latency_samples(recorded_at_ms DESC, action, status);
"""


def _path(app) -> Path:
    return Path(app.data_dir) / "solana_sibot.sqlite3"


def _connect(app):
    path = _path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _f(value):
    try:
        value = float(value)
        if value < 0:
            return None
        return round(value, 3)
    except (TypeError, ValueError):
        return None


def record_sample(
    app,
    *,
    attempt_key: str,
    telegram_id: str,
    action: str,
    status: str,
    receive_delay_ms=None,
    strategy_ms=None,
    latency: dict | None = None,
    error: str = "",
) -> None:
    latency = dict(latency or {})
    execution_total = _f(latency.get("execution_total_ms"))
    strategy = _f(strategy_ms)
    receive = _f(receive_delay_ms)
    total_event = None
    if receive is not None and strategy is not None and execution_total is not None:
        total_event = round(receive + strategy + execution_total, 3)

    values = {
        "receive_delay_ms": receive,
        "strategy_ms": strategy,
        "pre_balance_ms": _f(latency.get("pre_balance_ms")),
        "order_ms": _f(latency.get("order_ms")),
        "transaction_construction_ms": _f(latency.get("transaction_construction_ms")),
        "simulation_ms": _f(latency.get("simulation_ms")),
        "execute_ms": _f(latency.get("execute_ms")),
        "post_balance_ms": _f(latency.get("post_balance_ms")),
        "execution_total_ms": execution_total,
        "total_event_to_result_ms": total_event,
    }
    now_ms = time.time_ns() // 1_000_000
    try:
        with _connect(app) as conn:
            conn.execute(
                """INSERT INTO execution_latency_samples(
                     attempt_key,recorded_at_ms,telegram_id,action,status,
                     receive_delay_ms,strategy_ms,pre_balance_ms,order_ms,
                     transaction_construction_ms,simulation_ms,execute_ms,
                     post_balance_ms,execution_total_ms,total_event_to_result_ms,error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(attempt_key) DO UPDATE SET
                     recorded_at_ms=excluded.recorded_at_ms,
                     status=excluded.status,
                     receive_delay_ms=COALESCE(excluded.receive_delay_ms,execution_latency_samples.receive_delay_ms),
                     strategy_ms=COALESCE(excluded.strategy_ms,execution_latency_samples.strategy_ms),
                     pre_balance_ms=COALESCE(excluded.pre_balance_ms,execution_latency_samples.pre_balance_ms),
                     order_ms=COALESCE(excluded.order_ms,execution_latency_samples.order_ms),
                     transaction_construction_ms=COALESCE(excluded.transaction_construction_ms,execution_latency_samples.transaction_construction_ms),
                     simulation_ms=COALESCE(excluded.simulation_ms,execution_latency_samples.simulation_ms),
                     execute_ms=COALESCE(excluded.execute_ms,execution_latency_samples.execute_ms),
                     post_balance_ms=COALESCE(excluded.post_balance_ms,execution_latency_samples.post_balance_ms),
                     execution_total_ms=COALESCE(excluded.execution_total_ms,execution_latency_samples.execution_total_ms),
                     total_event_to_result_ms=COALESCE(excluded.total_event_to_result_ms,execution_latency_samples.total_event_to_result_ms),
                     error=excluded.error""",
                (
                    str(attempt_key), int(now_ms), str(telegram_id), str(action).upper(), str(status).upper(),
                    values["receive_delay_ms"], values["strategy_ms"], values["pre_balance_ms"], values["order_ms"],
                    values["transaction_construction_ms"], values["simulation_ms"], values["execute_ms"],
                    values["post_balance_ms"], values["execution_total_ms"], values["total_event_to_result_ms"],
                    str(error or "")[:1200],
                ),
            )
    except Exception as exc:
        print("[execution-latency-record]", type(exc).__name__, str(exc)[:240])


def _percentile(values: list[float], q: float):
    if not values:
        return None
    values = sorted(float(v) for v in values)
    if len(values) == 1:
        return round(values[0], 3)
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(len(values) - 1, lo + 1)
    frac = pos - lo
    return round(values[lo] * (1 - frac) + values[hi] * frac, 3)


def _stats(rows: list[dict], key: str) -> dict:
    values = [float(r[key]) for r in rows if r.get(key) is not None and float(r[key]) >= 0]
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 3) if values else None,
    }


def summary(app, *, now: int | None = None) -> dict:
    current = int(now or time.time())
    cutoff_ms = (current - WINDOW_SECONDS) * 1000
    current_ms = (current - CURRENT_SECONDS) * 1000
    rows: list[dict] = []
    try:
        with _connect(app) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM execution_latency_samples WHERE recorded_at_ms>=? ORDER BY recorded_at_ms DESC LIMIT 5000",
                (int(cutoff_ms),),
            ).fetchall()]
    except Exception:
        rows = []

    latest = [r for r in rows if int(r.get("recorded_at_ms") or 0) >= current_ms]
    previous = [r for r in rows if int(r.get("recorded_at_ms") or 0) < current_ms]
    stage_24h = {stage: _stats(latest, stage) for stage in _STAGE_COLUMNS}
    stage_baseline = {stage: _stats(previous, stage) for stage in _STAGE_COLUMNS}

    construction_p95 = stage_24h["transaction_construction_ms"]["p95_ms"]
    order_p95 = stage_24h["order_ms"]["p95_ms"]
    simulation_p95 = stage_24h["simulation_ms"]["p95_ms"]
    execute_p95 = stage_24h["execute_ms"]["p95_ms"]

    if len(latest) < 5:
        conclusion = "BENCHMARK"
        reason = "Collect at least five 24h execution samples before any server-move decision."
    elif construction_p95 is not None and construction_p95 >= 25:
        conclusion = "BENCHMARK"
        reason = "Local transaction construction/signing p95 is high enough to benchmark dedicated high-frequency CPU before considering a move."
    elif max([v for v in (order_p95, simulation_p95, execute_p95) if v is not None] or [0]) >= 150:
        conclusion = "BENCHMARK"
        reason = "External order/RPC/execute latency dominates; benchmark Frankfurt, Amsterdam and London network paths before changing CPU class."
    else:
        conclusion = "KEEP"
        reason = "No measured high-resolution local bottleneck currently justifies a server move; keep collecting and compare against a candidate on the same workload."

    return {
        "available": bool(rows),
        "metric": "high_resolution_local_execution_stages_ms",
        "scope": "Solana LIVE attempts from local event receipt through strategy/preflight and transaction execution",
        "clock": "time.perf_counter_ns for local stages; chain event_ts is second-resolution so receive_delay_ms is coarse",
        "samples_24h": len(latest),
        "samples_7d": len(rows),
        "current_24h": stage_24h,
        "preceding_six_day_baseline": stage_baseline,
        "infrastructure_conclusion": conclusion,
        "recommendation": reason,
        "fast_server_comparison": {
            "candidate_regions": ["Frankfurt", "Amsterdam", "London"],
            "rule": "Do not call a candidate faster until the same workload is measured there. Compare p50/p95 stage-by-stage, cost, and execution outcome before MOVE.",
            "cpu_candidate": "Dedicated/high-frequency vCPU with NVMe for transaction-construction tests",
            "network_candidate": "Region colocated with Solana/Jito/Helius European endpoints when order/simulation/execute dominates",
            "fresh_research_required": True,
        },
        "measurement_limits": [
            "receive_delay_ms uses a second-resolution chain event timestamp and is not sub-millisecond network propagation evidence",
            "strategy_ms includes all local strategy/preflight work before the LIVE executor is called, including any external quote/RPC waits inside those checks",
            "transaction_construction_ms is measured from completed Jupiter order to simulation start and includes decode/sign/base64 construction",
            "execute_ms includes Jupiter execute request/response until the post-execution balance check begins; it is not pure validator inclusion time",
        ],
    }
