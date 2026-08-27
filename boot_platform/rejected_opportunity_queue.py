from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ROOT = Path(
    os.environ.get(
        "BOOT_REJECTED_OPPORTUNITY_DIR",
        "/home/ayman01323/BOOT/data/candidates/REJECTED OPPORTUNITY",
    )
)
DB_NAME = "high_risk_queue.sqlite3"
CSV_NAME = "high_risk_opportunities.csv"

HARD_BLOCK_TERMS = {
    "HONEYPOT",
    "NO_SELL",
    "TOKEN_SECURITY_SEVERE",
    "FREEZE_AUTHORITY",
    "MINT_AUTHORITY",
    "MALICIOUS_TRANSFER",
    "POOL_LIQUIDITY_COLLAPSE",
}


def _now() -> int:
    return int(time.time())


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _candidate_id(chain: str, token: str, pool: str = "") -> str:
    raw = f"{chain.strip().lower()}|{token.strip()}|{pool.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _material_hash(payload: dict[str, Any]) -> str:
    """Hash only material state so unchanged re-observations do not wake SiRisky."""
    keys = (
        "risk_class",
        "rejection_class",
        "lp_status",
        "lp_locked_pct",
        "liquidity_usd",
        "liquidity_velocity_pct",
        "reverse_sell_available",
        "reverse_impact_pct",
        "roundtrip_loss_pct",
        "stress_3x_impact_pct",
        "developer_selling",
        "developer_selling_known",
        "mint_authority_present",
        "freeze_authority_present",
        "honeypot",
        "simulation_ok",
    )
    canonical = {key: payload.get(key) for key in keys if key in payload}
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _observation_key(candidate_id: str, source_bot: str, source_event_id: str, reason: str, observed_at: int) -> str:
    # Collapse the same source event/reason, while still allowing a materially new
    # observation with a later timestamp when source_event_id is absent.
    epoch_bucket = observed_at if not source_event_id else 0
    raw = f"{candidate_id}|{source_bot}|{source_event_id}|{reason}|{epoch_bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hard_block(rejection_class: str, reason: str, payload: dict[str, Any]) -> bool:
    if bool(payload.get("honeypot")):
        return True
    text = f"{rejection_class} {reason}".upper()
    return any(term in text for term in HARD_BLOCK_TERMS)


def _sirisky_eligible(chain: str, rejection_class: str, reason: str, payload: dict[str, Any]) -> bool:
    if chain.strip().lower() != "solana":
        return False
    # Structural malicious/no-sell cases remain learning-only.  Conditional LP,
    # freshness, thin-liquidity and strategy-threshold cases may be investigated.
    return not _hard_block(rejection_class, reason, payload)


@dataclass(frozen=True)
class PublishResult:
    candidate_id: str
    status: str
    generation: int
    inserted_observation: bool
    sirisky_eligible: bool


class RejectedOpportunityQueue:
    """Durable, indexed, multi-writer rejected-opportunity queue.

    This class is coordination/data only. It never signs, broadcasts, mutates a
    wallet, reserves capital, or changes a bot's risk settings.
    """

    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.db_path = self.root / DB_NAME
        self.csv_path = self.root / CSV_NAME
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates(
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL UNIQUE,
                    chain TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    pool_address TEXT NOT NULL DEFAULT '',
                    dex TEXT NOT NULL DEFAULT '',
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    status TEXT NOT NULL DEFAULT 'NEW',
                    generation INTEGER NOT NULL DEFAULT 1,
                    claimed_by TEXT,
                    claimed_at INTEGER,
                    claim_expires_at INTEGER,
                    current_risk_class TEXT NOT NULL DEFAULT '',
                    material_state_hash TEXT NOT NULL DEFAULT '',
                    sirisky_eligible INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS refusal_observations(
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_key TEXT NOT NULL UNIQUE,
                    candidate_id TEXT NOT NULL,
                    source_bot TEXT NOT NULL,
                    source_strategy_id TEXT NOT NULL DEFAULT '',
                    source_event_id TEXT NOT NULL DEFAULT '',
                    observed_at INTEGER NOT NULL,
                    rejection_class TEXT NOT NULL DEFAULT '',
                    rejection_reason TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE TABLE IF NOT EXISTS sirisky_decisions(
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    worker_id TEXT NOT NULL,
                    examined_at INTEGER NOT NULL,
                    strategy_buy_id TEXT NOT NULL DEFAULT '',
                    strategy_sell_id TEXT NOT NULL DEFAULT '',
                    temperature TEXT NOT NULL DEFAULT '',
                    risk_score REAL,
                    entry_score REAL,
                    hard_block INTEGER NOT NULL DEFAULT 0,
                    hard_block_reason TEXT NOT NULL DEFAULT '',
                    reverse_sell_ok INTEGER,
                    reverse_impact_pct REAL,
                    simulation_ok INTEGER,
                    decision TEXT NOT NULL,
                    requested_capital REAL,
                    requested_order_size REAL,
                    decision_reason TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    UNIQUE(candidate_id, generation),
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE TABLE IF NOT EXISTS candidate_events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at INTEGER NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_work
                    ON candidates(sirisky_eligible,status,priority DESC,queue_id);
                CREATE INDEX IF NOT EXISTS idx_candidates_token
                    ON candidates(chain,token_address,pool_address);
                CREATE INDEX IF NOT EXISTS idx_candidates_updated
                    ON candidates(updated_at);
                CREATE INDEX IF NOT EXISTS idx_observations_candidate
                    ON refusal_observations(candidate_id,observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_source
                    ON refusal_observations(source_bot,observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_decisions_candidate
                    ON sirisky_decisions(candidate_id,generation DESC);
                """
            )

    def publish(
        self,
        *,
        chain: str,
        token_address: str,
        source_bot: str,
        rejection_reason: str,
        pool_address: str = "",
        dex: str = "",
        source_strategy_id: str = "",
        source_event_id: str = "",
        rejection_class: str = "STRATEGY_REJECT",
        priority: int = 50,
        observed_at: int | None = None,
        payload: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> PublishResult:
        chain = str(chain or "").strip().lower()
        token_address = str(token_address or "").strip()
        pool_address = str(pool_address or "").strip()
        source_bot = str(source_bot or "unknown").strip().lower()
        if not chain or not token_address:
            raise ValueError("chain and token_address are required")

        payload = dict(payload or {})
        payload.setdefault("rejection_class", rejection_class)
        cid = candidate_id or _candidate_id(chain, token_address, pool_address)
        now = int(observed_at or _now())
        state_hash = _material_hash(payload)
        eligible = _sirisky_eligible(chain, rejection_class, rejection_reason, payload)
        obs_key = _observation_key(cid, source_bot, source_event_id, rejection_reason, now)
        priority = max(0, min(100, int(priority)))

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
            if row is None:
                status = "NEW" if eligible else "HARD_BLOCK" if _hard_block(rejection_class, rejection_reason, payload) else "RECORDED"
                generation = 1
                conn.execute(
                    """INSERT INTO candidates(
                         candidate_id,chain,token_address,pool_address,dex,first_seen_at,last_seen_at,
                         priority,status,generation,current_risk_class,material_state_hash,sirisky_eligible,
                         created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cid, chain, token_address, pool_address, str(dex or ""), now, now,
                        priority, status, generation, str(payload.get("risk_class") or rejection_class),
                        state_hash, int(eligible), now, now,
                    ),
                )
                conn.execute(
                    "INSERT INTO candidate_events(candidate_id,generation,event_type,event_at,detail_json) VALUES(?,?,?,?,?)",
                    (cid, generation, "CREATED", now, _json({"source_bot": source_bot, "status": status})),
                )
            else:
                generation = int(row["generation"])
                status = str(row["status"])
                materially_changed = bool(state_hash and state_hash != str(row["material_state_hash"] or ""))
                wakeable = status in {"SIRISKY_REJECT", "SIRISKY_PASS", "EXPIRED", "RECORDED"}
                if eligible and materially_changed and wakeable:
                    generation += 1
                    status = "RECHECK"
                    conn.execute(
                        "INSERT INTO candidate_events(candidate_id,generation,event_type,event_at,detail_json) VALUES(?,?,?,?,?)",
                        (cid, generation, "MATERIAL_CHANGE", now, _json({"source_bot": source_bot})),
                    )
                elif not eligible and _hard_block(rejection_class, rejection_reason, payload):
                    status = "HARD_BLOCK"
                conn.execute(
                    """UPDATE candidates SET last_seen_at=?,priority=?,status=?,generation=?,dex=?,
                       current_risk_class=?,material_state_hash=?,sirisky_eligible=?,updated_at=?
                       WHERE candidate_id=?""",
                    (
                        now, max(priority, int(row["priority"])), status, generation, str(dex or row["dex"] or ""),
                        str(payload.get("risk_class") or rejection_class), state_hash or str(row["material_state_hash"] or ""),
                        int(eligible), now, cid,
                    ),
                )

            inserted = True
            try:
                conn.execute(
                    """INSERT INTO refusal_observations(
                         observation_key,candidate_id,source_bot,source_strategy_id,source_event_id,
                         observed_at,rejection_class,rejection_reason,payload_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        obs_key, cid, source_bot, str(source_strategy_id or ""), str(source_event_id or ""),
                        now, str(rejection_class or ""), str(rejection_reason or "")[:2000], _json(payload), _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                inserted = False
            conn.execute("COMMIT")

        return PublishResult(cid, status, generation, inserted, eligible)

    def release_expired_claims(self, now: int | None = None) -> int:
        now = int(now or _now())
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE candidates
                   SET status=CASE WHEN generation>1 THEN 'RECHECK' ELSE 'NEW' END,
                       claimed_by=NULL,claimed_at=NULL,claim_expires_at=NULL,updated_at=?
                   WHERE status='CLAIMED' AND claim_expires_at IS NOT NULL AND claim_expires_at<?""",
                (now, now),
            )
            return int(cur.rowcount or 0)

    def claim(self, worker_id: str, *, limit: int = 1, lease_seconds: int = 60) -> list[dict[str, Any]]:
        worker_id = str(worker_id or "").strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        limit = max(1, min(100, int(limit)))
        lease_seconds = max(10, min(3600, int(lease_seconds)))
        now = _now()
        out: list[dict[str, Any]] = []

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE candidates
                   SET status=CASE WHEN generation>1 THEN 'RECHECK' ELSE 'NEW' END,
                       claimed_by=NULL,claimed_at=NULL,claim_expires_at=NULL,updated_at=?
                   WHERE status='CLAIMED' AND claim_expires_at IS NOT NULL AND claim_expires_at<?""",
                (now, now),
            )
            rows = conn.execute(
                """SELECT * FROM candidates
                   WHERE sirisky_eligible=1 AND status IN ('NEW','RECHECK')
                   ORDER BY priority DESC,queue_id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            for row in rows:
                cid = str(row["candidate_id"])
                cur = conn.execute(
                    """UPDATE candidates SET status='CLAIMED',claimed_by=?,claimed_at=?,claim_expires_at=?,updated_at=?
                       WHERE candidate_id=? AND status IN ('NEW','RECHECK')""",
                    (worker_id, now, now + lease_seconds, now, cid),
                )
                if cur.rowcount != 1:
                    continue
                observations = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM refusal_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 25",
                        (cid,),
                    ).fetchall()
                ]
                item = dict(row)
                item.update({"status": "CLAIMED", "claimed_by": worker_id, "claimed_at": now, "claim_expires_at": now + lease_seconds})
                item["observations"] = observations
                out.append(item)
                conn.execute(
                    "INSERT INTO candidate_events(candidate_id,generation,event_type,event_at,detail_json) VALUES(?,?,?,?,?)",
                    (cid, int(row["generation"]), "CLAIMED", now, _json({"worker_id": worker_id})),
                )
            conn.execute("COMMIT")
        return out

    def decide(
        self,
        *,
        candidate_id: str,
        worker_id: str,
        decision: str,
        strategy_buy_id: str = "",
        strategy_sell_id: str = "",
        decision_reason: str = "",
        temperature: str = "",
        risk_score: float | None = None,
        entry_score: float | None = None,
        hard_block: bool = False,
        hard_block_reason: str = "",
        reverse_sell_ok: bool | None = None,
        reverse_impact_pct: float | None = None,
        simulation_ok: bool | None = None,
        requested_capital: float | None = None,
        requested_order_size: float | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = str(candidate_id)
        worker_id = str(worker_id)
        decision = str(decision).upper().strip()
        if decision not in {"PASS", "REJECT", "HARD_BLOCK"}:
            raise ValueError("decision must be PASS, REJECT, or HARD_BLOCK")
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(cid)
            if str(row["status"]) != "CLAIMED" or str(row["claimed_by"] or "") != worker_id:
                conn.execute("ROLLBACK")
                raise RuntimeError("candidate is not claimed by this worker")
            generation = int(row["generation"])
            final_status = "HARD_BLOCK" if decision == "HARD_BLOCK" or hard_block else f"SIRISKY_{decision}"
            conn.execute(
                """INSERT OR REPLACE INTO sirisky_decisions(
                     candidate_id,generation,worker_id,examined_at,strategy_buy_id,strategy_sell_id,
                     temperature,risk_score,entry_score,hard_block,hard_block_reason,reverse_sell_ok,
                     reverse_impact_pct,simulation_ok,decision,requested_capital,requested_order_size,
                     decision_reason,evidence_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, generation, worker_id, now, str(strategy_buy_id or ""), str(strategy_sell_id or ""),
                    str(temperature or ""), risk_score, entry_score, int(bool(hard_block or decision == "HARD_BLOCK")),
                    str(hard_block_reason or ""), None if reverse_sell_ok is None else int(bool(reverse_sell_ok)),
                    reverse_impact_pct, None if simulation_ok is None else int(bool(simulation_ok)), decision,
                    requested_capital, requested_order_size, str(decision_reason or "")[:2000], _json(evidence or {}), now,
                ),
            )
            conn.execute(
                """UPDATE candidates SET status=?,claimed_by=NULL,claimed_at=NULL,claim_expires_at=NULL,updated_at=?
                   WHERE candidate_id=?""",
                (final_status, now, cid),
            )
            conn.execute(
                "INSERT INTO candidate_events(candidate_id,generation,event_type,event_at,detail_json) VALUES(?,?,?,?,?)",
                (cid, generation, final_status, now, _json({"worker_id": worker_id, "reason": decision_reason})),
            )
            conn.execute("COMMIT")
            return {"candidate_id": cid, "generation": generation, "status": final_status}

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT status,COUNT(*) n FROM candidates GROUP BY status ORDER BY status").fetchall()
            out = {str(r["status"]): int(r["n"]) for r in rows}
            out["TOTAL"] = sum(out.values())
            out["OBSERVATIONS"] = int(conn.execute("SELECT COUNT(*) FROM refusal_observations").fetchone()[0])
            out["DECISIONS"] = int(conn.execute("SELECT COUNT(*) FROM sirisky_decisions").fetchone()[0])
            return out

    def export_csv(self) -> Path:
        fields = [
            "queue_id", "candidate_id", "chain", "token_address", "pool_address", "dex",
            "first_seen_at", "last_seen_at", "priority", "status", "generation", "current_risk_class",
            "sirisky_eligible", "claimed_by", "claimed_at", "claim_expires_at", "updated_at",
        ]
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM candidates ORDER BY queue_id").fetchall()
        tmp = self.csv_path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                d = dict(row)
                writer.writerow({key: d.get(key, "") for key in fields})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.csv_path)
        return self.csv_path


def safe_publish_rejection(**kwargs) -> PublishResult | None:
    """Best-effort telemetry hook: a queue failure must never change trade logic."""
    try:
        root = kwargs.pop("root", DEFAULT_ROOT)
        return RejectedOpportunityQueue(root).publish(**kwargs)
    except Exception as exc:
        print(f"[rejected-opportunity] publish_failed={type(exc).__name__}: {exc}")
        return None


def _cli() -> int:
    parser = argparse.ArgumentParser(description="BOOT rejected opportunity queue")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
    sub.add_parser("export")

    claim = sub.add_parser("claim")
    claim.add_argument("--worker", required=True)
    claim.add_argument("--limit", type=int, default=1)
    claim.add_argument("--lease-seconds", type=int, default=60)

    publish = sub.add_parser("publish-json")
    publish.add_argument("json_file")

    decide = sub.add_parser("decide-json")
    decide.add_argument("json_file")

    args = parser.parse_args()
    q = RejectedOpportunityQueue(args.root)
    if args.command == "init":
        print(q.db_path)
    elif args.command == "stats":
        print(json.dumps(q.stats(), sort_keys=True))
    elif args.command == "export":
        print(q.export_csv())
    elif args.command == "claim":
        print(json.dumps(q.claim(args.worker, limit=args.limit, lease_seconds=args.lease_seconds), default=_json_default))
    elif args.command == "publish-json":
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        print(q.publish(**payload))
    elif args.command == "decide-json":
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        print(json.dumps(q.decide(**payload), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
