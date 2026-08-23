from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

LANES = {"ENGINEERING", "STRATEGY"}
FINDING_TYPES = {"PROBLEM", "OPPORTUNITY"}
CLASSIFICATIONS = {"STRATEGY", "MARKET", "EXECUTION", "INFRASTRUCTURE", "DATA", "RESEARCH"}
SEVERITIES = {"P0", "P1", "P2", "P3", "INFO"}
PACKAGE_STATES = {"QUEUED", "REVIEWING", "REVIEWED", "HUMAN_APPROVAL_REQUIRED", "CLOSED"}

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS monitor_findings(
  finding_id TEXT PRIMARY KEY,
  lane TEXT NOT NULL,
  finding_type TEXT NOT NULL,
  classification TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  strategy_id TEXT NOT NULL DEFAULT '',
  source_version TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL,
  recommendation TEXT NOT NULL DEFAULT '',
  acceptance_test TEXT NOT NULL DEFAULT '',
  first_seen_epoch INTEGER NOT NULL,
  last_seen_epoch INTEGER NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS factory_packages(
  package_id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL UNIQUE,
  lane TEXT NOT NULL,
  severity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'QUEUED',
  review_json TEXT NOT NULL DEFAULT '{}',
  created_epoch INTEGER NOT NULL,
  updated_epoch INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitor_findings_open
ON monitor_findings(status,lane,severity,last_seen_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_factory_packages_queue
ON factory_packages(state,severity,created_epoch);
"""


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prefix: str, value: Any, size: int = 20) -> str:
    raw = _canonical(value).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:size]


def _db_path(app) -> Path:
    return Path(app.data_dir) / "monitor_factory.sqlite3"


def connect(app):
    path = _db_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _normalise(value: str, allowed: set[str], label: str) -> str:
    text = str(value or "").strip().upper()
    if text not in allowed:
        raise ValueError(f"unsupported {label}: {text}")
    return text


def record_finding(
    app,
    *,
    lane: str,
    finding_type: str,
    classification: str,
    severity: str,
    title: str,
    evidence: dict,
    scope: str = "",
    strategy_id: str = "",
    source_version: str = "",
    recommendation: str = "",
    acceptance_test: str = "",
    now: int | None = None,
) -> dict:
    """Record/dedupe one monitor conclusion without changing trading state."""
    lane = _normalise(lane, LANES, "lane")
    finding_type = _normalise(finding_type, FINDING_TYPES, "finding type")
    classification = _normalise(classification, CLASSIFICATIONS, "classification")
    severity = _normalise(severity, SEVERITIES, "severity")
    title = " ".join(str(title or "").split())[:300]
    if not title:
        raise ValueError("finding title is required")
    evidence = dict(evidence or {})
    now = int(now or time.time())
    identity = {
        "lane": lane,
        "finding_type": finding_type,
        "classification": classification,
        "title": title.casefold(),
        "scope": str(scope or "")[:240],
        "strategy_id": str(strategy_id or "")[:120],
        "source_version": str(source_version or "")[:160],
    }
    finding_id = _digest("finding_", identity)
    with closing(connect(app)) as conn:
        old = conn.execute("SELECT occurrences,first_seen_epoch FROM monitor_findings WHERE finding_id=?", (finding_id,)).fetchone()
        occurrences = int(old["occurrences"] or 0) + 1 if old else 1
        first_seen = int(old["first_seen_epoch"] or now) if old else now
        conn.execute(
            """INSERT INTO monitor_findings(
                 finding_id,lane,finding_type,classification,severity,title,scope,strategy_id,source_version,
                 evidence_json,recommendation,acceptance_test,first_seen_epoch,last_seen_epoch,occurrences,status
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')
               ON CONFLICT(finding_id) DO UPDATE SET
                 severity=excluded.severity,evidence_json=excluded.evidence_json,
                 recommendation=excluded.recommendation,acceptance_test=excluded.acceptance_test,
                 last_seen_epoch=excluded.last_seen_epoch,occurrences=excluded.occurrences,status='OPEN'""",
            (
                finding_id, lane, finding_type, classification, severity, title,
                str(scope or "")[:240], str(strategy_id or "")[:120], str(source_version or "")[:160],
                _canonical(evidence), str(recommendation or "")[:1800], str(acceptance_test or "")[:1800],
                first_seen, now, occurrences,
            ),
        )
        conn.commit()
    return {
        "finding_id": finding_id,
        "lane": lane,
        "finding_type": finding_type,
        "classification": classification,
        "severity": severity,
        "title": title,
        "scope": str(scope or "")[:240],
        "strategy_id": str(strategy_id or "")[:120],
        "source_version": str(source_version or "")[:160],
        "evidence": evidence,
        "recommendation": str(recommendation or "")[:1800],
        "acceptance_test": str(acceptance_test or "")[:1800],
        "first_seen_epoch": first_seen,
        "last_seen_epoch": now,
        "occurrences": occurrences,
        "status": "OPEN",
    }


def package_for_finding(app, finding: dict, *, now: int | None = None) -> dict:
    """Turn a monitor finding into the shared Problem/Opportunity Package."""
    now = int(now or time.time())
    finding_id = str(finding.get("finding_id") or "")
    if not finding_id:
        raise ValueError("finding_id is required")
    package_id = _digest("pkg_", {"finding_id": finding_id})
    payload = {
        "schema_version": 1,
        "package_id": package_id,
        "finding_id": finding_id,
        "lane": str(finding.get("lane") or ""),
        "type": str(finding.get("finding_type") or ""),
        "classification": str(finding.get("classification") or ""),
        "severity": str(finding.get("severity") or ""),
        "title": str(finding.get("title") or ""),
        "scope": str(finding.get("scope") or ""),
        "strategy_id": str(finding.get("strategy_id") or ""),
        "source_version": str(finding.get("source_version") or ""),
        "evidence": dict(finding.get("evidence") or {}),
        "recommended_investigation": str(finding.get("recommendation") or ""),
        "acceptance_test": str(finding.get("acceptance_test") or ""),
        "objective": "Improve durable money-weighted net P&L after all recorded costs while preserving correctness, execution safety and capital controls.",
        "secondary_kpis": [
            "win_rate_pct",
            "wins_vs_losses_count",
            "gross_profit_vs_gross_loss_value",
            "profit_factor",
            "net_profit_after_costs",
            "eligible_opportunity_participation",
            "execution_failure_rate",
            "latency_and_infrastructure_cost",
        ],
        "decision_rule": "Objective evidence outranks model agreement; win rate/count are secondary and cannot override negative money-weighted economics.",
        "factory_authority": {
            "research": True,
            "propose_shadow_change": True,
            "draft_pr": True,
            "trade": False,
            "arm_live": False,
            "change_capital": False,
            "change_wallet_or_signing": False,
            "bypass_safety_gate": False,
            "merge_or_deploy_without_existing_authorisation": False,
        },
        "promotion_path": [
            "EXPERIMENT",
            "SHADOW",
            "PROMOTION_CANDIDATE",
            "MASTER_CANARY_APPROVAL",
            "CANARY",
            "READY_FOR_FULL_LIVE",
            "MASTER_FULL_LIVE_APPROVAL",
            "FULL_LIVE",
            "CONTINUOUS_MONITORING",
        ],
        "created_epoch": now,
    }
    with closing(connect(app)) as conn:
        existing = conn.execute("SELECT state,created_epoch FROM factory_packages WHERE package_id=?", (package_id,)).fetchone()
        state = str(existing["state"] or "QUEUED") if existing else "QUEUED"
        created = int(existing["created_epoch"] or now) if existing else now
        conn.execute(
            """INSERT INTO factory_packages(package_id,finding_id,lane,severity,payload_json,state,review_json,created_epoch,updated_epoch)
               VALUES(?,?,?,?,?,'QUEUED','{}',?,?)
               ON CONFLICT(package_id) DO UPDATE SET
                 lane=excluded.lane,severity=excluded.severity,payload_json=excluded.payload_json,updated_epoch=excluded.updated_epoch""",
            (package_id, finding_id, payload["lane"], payload["severity"], _canonical(payload), created, now),
        )
        conn.commit()
    return {"package_id": package_id, "state": state, "payload": payload}


def queue_finding(app, finding: dict, *, now: int | None = None) -> dict:
    return package_for_finding(app, finding, now=now)


def pending_packages(app, *, limit: int = 20, severities: set[str] | None = None) -> list[dict]:
    sql = "SELECT * FROM factory_packages WHERE state='QUEUED'"
    args: list[Any] = []
    if severities:
        ordered = sorted(_normalise(x, SEVERITIES, "severity") for x in severities)
        sql += " AND severity IN (" + ",".join("?" for _ in ordered) + ")"
        args.extend(ordered)
    rank = "CASE severity WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END"
    sql += f" ORDER BY {rank}, created_epoch LIMIT ?"
    args.append(max(1, int(limit)))
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    out = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        out.append({**row, "payload": payload})
    return out


def set_package_state(app, package_id: str, state: str, *, review: dict | None = None, now: int | None = None) -> None:
    state = _normalise(state, PACKAGE_STATES, "package state")
    now = int(now or time.time())
    with closing(connect(app)) as conn:
        if not conn.execute("SELECT 1 FROM factory_packages WHERE package_id=?", (str(package_id),)).fetchone():
            raise ValueError("unknown package_id")
        conn.execute(
            "UPDATE factory_packages SET state=?,review_json=?,updated_epoch=? WHERE package_id=?",
            (state, _canonical(review or {}), now, str(package_id)),
        )
        conn.commit()


def _portfolio_kpis(portfolio: dict) -> dict:
    totals = {
        "strategies": 0,
        "windows": 0,
        "opportunities": 0,
        "eligible_opportunities": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "gross_profit": Decimal(0),
        "gross_loss": Decimal(0),
        "fees": Decimal(0),
        "slippage_cost": Decimal(0),
        "net_profit": Decimal(0),
        "execution_failures": 0,
    }
    for row in (portfolio or {}).get("strategies") or []:
        metrics = (row or {}).get("metrics") or {}
        totals["strategies"] += 1
        for key in ("windows", "opportunities", "eligible_opportunities", "trades", "wins", "losses", "execution_failures"):
            totals[key] += int(metrics.get(key) or 0)
        for key in ("gross_profit", "gross_loss", "fees", "slippage_cost", "net_profit"):
            totals[key] += _d(metrics.get(key))
    trades = int(totals["trades"])
    wins = int(totals["wins"])
    losses = int(totals["losses"])
    gp = totals["gross_profit"]
    gl = totals["gross_loss"]
    pf = gp / gl if gl > 0 else (Decimal("99") if gp > 0 else Decimal(0))
    return {
        "strategies": int(totals["strategies"]),
        "windows": int(totals["windows"]),
        "opportunities": int(totals["opportunities"]),
        "eligible_opportunities": int(totals["eligible_opportunities"]),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round((wins / trades * 100.0), 2) if trades else None,
        "wins_exceed_losses_count": wins > losses if trades else None,
        "gross_profit": str(gp),
        "gross_loss": str(gl),
        "gross_profit_exceeds_gross_loss": gp > gl if trades else None,
        "fees": str(totals["fees"]),
        "slippage_cost": str(totals["slippage_cost"]),
        "net_profit": str(totals["net_profit"]),
        "profit_factor": str(pf),
        "execution_failures": int(totals["execution_failures"]),
        "primary_target_pass": bool(trades and totals["net_profit"] > 0 and pf > Decimal(1)),
        "secondary_three_way_target_pass": bool(trades and wins > losses and gp > gl),
    }


def run_strategy_monitor(app, *, now: int | None = None) -> dict:
    """Evaluate Strategy Lab evidence and enqueue only material conclusions."""
    from . import strategy_lab

    now = int(now or time.time())
    strategy_lab.seed_creative_hypotheses(app)
    portfolio = strategy_lab.portfolio_report(app)
    findings: list[dict] = []
    for decision in portfolio.get("strategies") or []:
        metrics = decision.get("metrics") or {}
        strategy_id = str(decision.get("strategy_id") or "")
        source_version = str(decision.get("previous_status") or "") + "->" + str(decision.get("status") or "")
        execution_failures = int(metrics.get("execution_failures") or 0)
        if execution_failures:
            findings.append(record_finding(
                app,
                lane="STRATEGY",
                finding_type="PROBLEM",
                classification="EXECUTION",
                severity="P1" if execution_failures >= 2 else "P2",
                title=f"{decision.get('name')}: execution failures require root-cause separation",
                scope=str(decision.get("family") or ""),
                strategy_id=strategy_id,
                source_version=source_version,
                evidence={"decision": decision, "execution_failures": execution_failures},
                recommendation="Engineering Monitor should determine whether the observed loss/failure is execution/infrastructure rather than changing strategy thresholds.",
                acceptance_test="Re-run the affected path with no unresolved execution failure and preserve all existing quote, simulation, liquidity, reserve, signing and reconciliation gates.",
                now=now,
            ))
        action = str(decision.get("action") or "")
        if action in {"REPLACE_OR_REWORK", "REWORK_FILTERS"}:
            findings.append(record_finding(
                app,
                lane="STRATEGY",
                finding_type="PROBLEM",
                classification="STRATEGY",
                severity="P2",
                title=f"{decision.get('name')}: {action}",
                scope=str(decision.get("family") or ""),
                strategy_id=strategy_id,
                source_version=source_version,
                evidence={"decision": decision},
                recommendation="Send to Strategy Factory for a falsifiable SHADOW rework/replacement hypothesis; do not loosen common execution-safety controls to manufacture trades.",
                acceptance_test="New or revised hypothesis must beat the current version out-of-sample after all recorded costs and meet the Strategy Lab sample/participation rules.",
                now=now,
            ))
        elif action == "PROMOTION_CANDIDATE":
            findings.append(record_finding(
                app,
                lane="STRATEGY",
                finding_type="OPPORTUNITY",
                classification="STRATEGY",
                severity="P2",
                title=f"{decision.get('name')}: SHADOW evidence reached promotion-candidate quality",
                scope=str(decision.get("family") or ""),
                strategy_id=strategy_id,
                source_version=source_version,
                evidence={"decision": decision},
                recommendation="Prepare the independent Engineering/research/canary readiness package. Promotion candidate is not LIVE authority.",
                acceptance_test="All existing SHADOW evidence gates plus Engineering, research freshness, common LIVE preflight and explicit MASTER canary approval must pass before any real-funds canary.",
                now=now,
            ))

    kpis = _portfolio_kpis(portfolio)
    if int(kpis["trades"] or 0) >= 8 and not bool(kpis["primary_target_pass"]):
        findings.append(record_finding(
            app,
            lane="STRATEGY",
            finding_type="PROBLEM",
            classification="STRATEGY",
            severity="P1" if _d(kpis["net_profit"]) < 0 else "P2",
            title="Portfolio economics are not meeting the primary money-weighted target",
            scope="ALL_STRATEGIES",
            evidence={"portfolio_kpis": kpis},
            recommendation="Factory should identify which strategy families and execution leaks dominate losses; improve or replace the weak contributors rather than optimising raw win count in isolation.",
            acceptance_test="A fresh, adequately sampled evaluation must show positive net P&L after costs and profit factor above 1 without a new execution-safety regression.",
            now=now,
        ))
    elif int(kpis["trades"] or 0) >= 8 and not bool(kpis["secondary_three_way_target_pass"]):
        findings.append(record_finding(
            app,
            lane="STRATEGY",
            finding_type="OPPORTUNITY",
            classification="STRATEGY",
            severity="P3",
            title="Secondary win-count/value target is weaker than the primary economics",
            scope="ALL_STRATEGIES",
            evidence={"portfolio_kpis": kpis},
            recommendation="Investigate whether loss distribution can be improved without sacrificing positive net economics. Do not optimise win rate at the cost of larger tail losses.",
            acceptance_test="Any proposed SHADOW change must improve risk-adjusted money-weighted results; win rate/count/value are supporting KPIs only.",
            now=now,
        ))

    packages = [queue_finding(app, row, now=now) for row in findings]
    status = {
        "schema_version": 1,
        "lane": "STRATEGY",
        "generated_epoch": now,
        "portfolio_kpis": kpis,
        "portfolio_totals": portfolio.get("totals") or {},
        "findings": findings,
        "packages_queued_or_refreshed": [p["package_id"] for p in packages],
        "authority": "MONITOR_AND_ESCALATE_ONLY",
        "live_auto_promote": False,
    }
    _write_lane_status(app, "strategy", status)
    return status


def _network_counters() -> dict:
    total_rx = total_tx = 0
    interfaces: dict[str, dict[str, int]] = {}
    try:
        lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
    except Exception:
        lines = []
    for line in lines:
        if ":" not in line:
            continue
        name, raw = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        fields = raw.split()
        if len(fields) < 9:
            continue
        rx = int(fields[0]); tx = int(fields[8])
        interfaces[name] = {"rx_bytes": rx, "tx_bytes": tx}
        total_rx += rx; total_tx += tx
    return {"rx_bytes_total": total_rx, "tx_bytes_total": total_tx, "interfaces": interfaces}


def _bandwidth_sample(app, now: int) -> dict:
    path = Path(app.data_dir) / "monitor_factory" / "bandwidth_state.json"
    current = {"generated_epoch": now, **_network_counters()}
    previous = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    elapsed = max(0, now - int(previous.get("generated_epoch") or 0))
    rx_delta = tx_delta = None
    if elapsed > 0:
        crx = int(current["rx_bytes_total"]); ctx = int(current["tx_bytes_total"])
        prx = int(previous.get("rx_bytes_total") or 0); ptx = int(previous.get("tx_bytes_total") or 0)
        if crx >= prx and ctx >= ptx:
            rx_delta = crx - prx; tx_delta = ctx - ptx
    total_delta = (rx_delta + tx_delta) if rx_delta is not None and tx_delta is not None else None
    bytes_per_hour = (total_delta * 3600.0 / elapsed) if total_delta is not None and elapsed else None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {
        "elapsed_seconds": elapsed or None,
        "rx_delta_bytes": rx_delta,
        "tx_delta_bytes": tx_delta,
        "total_delta_bytes": total_delta,
        "bytes_per_hour": round(bytes_per_hour, 2) if bytes_per_hour is not None else None,
        "megabytes_per_hour": round(bytes_per_hour / 1024 / 1024, 3) if bytes_per_hour is not None else None,
        "plan_limit_known": False,
        "note": "Host-level non-loopback counters; not attributed to one process. Compare with provider plan before calling usage excessive.",
    }


def run_engineering_monitor(app, *, now: int | None = None) -> dict:
    """Collect bounded infrastructure/execution evidence and queue material findings."""
    from . import execution_latency

    now = int(now or time.time())
    latency = execution_latency.summary(app, now=now)
    bandwidth = _bandwidth_sample(app, now)
    total, used, free = shutil.disk_usage("/")
    disk_used_pct = round((used / total * 100.0) if total else 0.0, 2)
    findings: list[dict] = []

    if disk_used_pct >= 90:
        findings.append(record_finding(
            app,
            lane="ENGINEERING", finding_type="PROBLEM", classification="INFRASTRUCTURE", severity="P1",
            title="Root filesystem usage is critical", scope="VPS",
            evidence={"disk_used_percent": disk_used_pct, "disk_free_bytes": int(free)},
            recommendation="Attribute growth and reclaim only verified disposable caches/artifacts; do not delete trading evidence or databases blindly.",
            acceptance_test="Disk usage returns below 85% and service/database integrity checks pass.", now=now,
        ))
    elif disk_used_pct >= 85:
        findings.append(record_finding(
            app,
            lane="ENGINEERING", finding_type="PROBLEM", classification="INFRASTRUCTURE", severity="P2",
            title="Root filesystem usage is high", scope="VPS",
            evidence={"disk_used_percent": disk_used_pct, "disk_free_bytes": int(free)},
            recommendation="Investigate the largest growing directories before the condition becomes service-affecting.",
            acceptance_test="Identify the growth source and restore an evidence-backed free-space margin without deleting required runtime evidence.", now=now,
        ))

    current = latency.get("current_24h") or {}
    baseline = latency.get("preceding_six_day_baseline") or {}
    regressed: list[dict] = []
    for stage in ("strategy_ms", "transaction_construction_ms", "order_ms", "simulation_ms", "execute_ms", "execution_total_ms"):
        c = current.get(stage) or {}; b = baseline.get(stage) or {}
        cp95 = c.get("p95_ms"); bp95 = b.get("p95_ms")
        if int(c.get("count") or 0) >= 5 and int(b.get("count") or 0) >= 5 and cp95 is not None and bp95 is not None and float(bp95) > 0:
            ratio = float(cp95) / float(bp95)
            if ratio >= 1.5 and float(cp95) - float(bp95) >= 10:
                regressed.append({"stage": stage, "current_p95_ms": cp95, "baseline_p95_ms": bp95, "ratio": round(ratio, 3)})
    if regressed:
        findings.append(record_finding(
            app,
            lane="ENGINEERING", finding_type="PROBLEM", classification="INFRASTRUCTURE", severity="P2",
            title="Measured execution latency regressed against the six-day baseline", scope="SOLANA_LIVE_EXECUTION",
            evidence={"regressions": regressed, "latency_summary": latency},
            recommendation="Separate local CPU/construction delay from external quote/RPC/execute delay before recommending a server or provider change.",
            acceptance_test="Same-workload p95 returns near baseline or a controlled candidate benchmark demonstrates a materially better result without execution regressions.", now=now,
        ))
    elif str(latency.get("infrastructure_conclusion") or "") == "BENCHMARK" and int(latency.get("samples_24h") or 0) >= 5:
        findings.append(record_finding(
            app,
            lane="ENGINEERING", finding_type="OPPORTUNITY", classification="INFRASTRUCTURE", severity="P3",
            title="Execution telemetry supports an infrastructure benchmark", scope="SOLANA_LIVE_EXECUTION",
            evidence={"latency_summary": latency, "bandwidth": bandwidth},
            recommendation=str(latency.get("recommendation") or "Benchmark the same workload before moving infrastructure."),
            acceptance_test="Compare p50/p95 stage-by-stage, execution outcome and cost on the same workload; do not call a server faster from geography/specification alone.", now=now,
        ))

    packages = [queue_finding(app, row, now=now) for row in findings]
    status = {
        "schema_version": 1,
        "lane": "ENGINEERING",
        "generated_epoch": now,
        "disk": {"used_percent": disk_used_pct, "free_bytes": int(free), "total_bytes": int(total)},
        "bandwidth": bandwidth,
        "execution_latency": latency,
        "findings": findings,
        "packages_queued_or_refreshed": [p["package_id"] for p in packages],
        "authority": "MONITOR_AND_RECOMMEND_ONLY",
        "changes_trading_state": False,
    }
    _write_lane_status(app, "engineering", status)
    return status


def _write_lane_status(app, lane: str, value: dict) -> None:
    path = Path(app.data_dir) / "monitor_factory" / f"{lane}_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def status_summary(app) -> dict:
    with closing(connect(app)) as conn:
        open_rows = [dict(r) for r in conn.execute(
            "SELECT lane,severity,COUNT(*) AS n FROM monitor_findings WHERE status='OPEN' GROUP BY lane,severity"
        ).fetchall()]
        package_rows = [dict(r) for r in conn.execute(
            "SELECT state,COUNT(*) AS n FROM factory_packages GROUP BY state"
        ).fetchall()]
    by_lane: dict[str, dict[str, int]] = {"ENGINEERING": {}, "STRATEGY": {}}
    for row in open_rows:
        by_lane.setdefault(str(row["lane"]), {})[str(row["severity"])] = int(row["n"])
    packages = {str(r["state"]): int(r["n"]) for r in package_rows}
    return {
        "generated_epoch": int(time.time()),
        "open_findings": by_lane,
        "packages": packages,
        "pending_total": int(packages.get("QUEUED", 0)),
        "live_authority": False,
    }
