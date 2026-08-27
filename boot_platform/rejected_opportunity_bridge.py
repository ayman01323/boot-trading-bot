from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .rejected_opportunity_queue import DEFAULT_ROOT, RejectedOpportunityQueue

BOT_IDS = ("learnerbot", "claude", "gpt", "gemini", "grok", "future")

# These are runtime sources that already exist in the current architecture. The
# bridge is deliberately read-only against them. Missing/unreadable sources are
# skipped rather than treated as empty evidence.
DEFAULT_SIBOT1_DBS = (
    Path("/root/multichain-learning-bot-v2.2-fast-direct-market/data/sibot1_solana_live_bridge.sqlite3"),
    Path("/home/ayman01323/ClaudeServer/runtime/data/sibot1_solana_live_bridge.sqlite3"),
)

MARKET_REJECTION_TERMS = (
    "POOL",
    "LIQUID",
    "LP_",
    "LP ",
    "RUGCHECK",
    "REVERSE",
    "SELLABILITY",
    "PRICE IMPACT",
    "ROUNDTRIP",
    "ROUND-TRIP",
    "DEX",
    "HONEYPOT",
    "FREEZE",
    "MINT AUTHORITY",
    "DEVELOPER",
    "DEV_",
    "SIMULATION",
    "QUOTE",
)

NON_MARKET_TERMS = (
    "ACCOUNT AUTOMATIC",
    "SIGNER",
    "NOT FUNDED",
    "INSUFFICIENT BALANCE",
    "PERMISSION IS OFF",
    "LIVE IS OFF",
    "AUTO IS OFF",
    "ARMED IS OFF",
)


def _market_rejection(error: str) -> bool:
    text = str(error or "").upper()
    if not text or any(term in text for term in NON_MARKET_TERMS):
        return False
    return any(term in text for term in MARKET_REJECTION_TERMS)


def _classify(error: str) -> str:
    text = str(error or "").upper()
    if "LP_CONCENTRATION_RISK" in text or "LP UNLOCK" in text:
        return "LP_CONCENTRATION_RISK"
    if "POOL_LIQUIDITY_COLLAPSE" in text:
        return "POOL_LIQUIDITY_COLLAPSE"
    if "RUGCHECK" in text or "TOKEN_SECURITY" in text:
        return "TOKEN_SECURITY_RISK"
    if "REVERSE" in text or "SELLABILITY" in text or "PRICE IMPACT" in text:
        return "EXIT_LIQUIDITY_RISK"
    if "SIMULATION" in text:
        return "SIMULATION_REJECT"
    if "QUOTE" in text:
        return "QUOTE_REJECT"
    return "MARKET_RISK_REJECT"


class RejectedOpportunityBridge:
    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.queue = RejectedOpportunityQueue(self.root)
        self.inbox = self.root / "inbox"
        self.processed = self.root / "processed"
        self.failed = self.root / "failed"
        self.state_path = self.root / "bridge_state.json"
        for bot in BOT_IDS:
            (self.inbox / bot).mkdir(parents=True, exist_ok=True)
            (self.processed / bot).mkdir(parents=True, exist_ok=True)
            (self.failed / bot).mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def consume_inboxes(self) -> dict[str, int]:
        counts = {"published": 0, "failed": 0}
        for bot in BOT_IDS:
            for path in sorted((self.inbox / bot).glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("payload must be an object")
                    payload.setdefault("source_bot", bot)
                    self.queue.publish(**payload)
                    shutil.move(str(path), str(self.processed / bot / path.name))
                    counts["published"] += 1
                except Exception as exc:
                    try:
                        path.with_suffix(path.suffix + ".error.txt").write_text(
                            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
                        )
                    except Exception:
                        pass
                    try:
                        shutil.move(str(path), str(self.failed / bot / path.name))
                    except Exception:
                        pass
                    counts["failed"] += 1
        return counts

    def ingest_sibot1_live_db(self, path: str | Path) -> dict[str, Any]:
        path = Path(path)
        result: dict[str, Any] = {"path": str(path), "readable": False, "published": 0, "skipped": 0}
        if not path.is_file() or not os.access(path, os.R_OK):
            return result
        result["readable"] = True
        key = f"sibot1:{path}"
        last = int(self.state.get(key, 0) or 0)
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT attempt_key,candidate_id,engine_id,chain,mint,status,error,updated_at
                   FROM attempts
                   WHERE updated_at>? AND kind='ENTRY' AND COALESCE(error,'')!=''
                   ORDER BY updated_at,attempt_key""",
                (last,),
            ).fetchall()
            conn.close()
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result

        high_water = last
        for row in rows:
            updated = int(row["updated_at"] or 0)
            high_water = max(high_water, updated)
            error = str(row["error"] or "")
            if not _market_rejection(error):
                result["skipped"] += 1
                continue
            engine = str(row["engine_id"] or "sibot1").strip().lower()
            token = str(row["mint"] or "").strip()
            chain = str(row["chain"] or "solana").strip().lower()
            if not token:
                result["skipped"] += 1
                continue
            self.queue.publish(
                chain=chain,
                token_address=token,
                source_bot=engine,
                source_strategy_id=engine,
                source_event_id=str(row["attempt_key"] or row["candidate_id"] or ""),
                rejection_class=_classify(error),
                rejection_reason=error,
                priority=80 if engine in {"gemini", "grok"} else 60,
                observed_at=updated or int(time.time()),
                payload={
                    "source_runtime": "sibot1_solana_live_bridge",
                    "source_candidate_id": str(row["candidate_id"] or ""),
                    "source_status": str(row["status"] or ""),
                    "risk_class": _classify(error),
                },
            )
            result["published"] += 1

        if high_water > last:
            self.state[key] = high_water
            self._save_state()
        return result

    def run_once(self, extra_sibot1_dbs: list[str] | None = None) -> dict[str, Any]:
        report: dict[str, Any] = {"inboxes": self.consume_inboxes(), "runtime_sources": []}
        paths = list(DEFAULT_SIBOT1_DBS)
        for value in extra_sibot1_dbs or []:
            paths.append(Path(value))
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            report["runtime_sources"].append(self.ingest_sibot1_live_db(path))
        report["queue"] = self.queue.stats()
        self.queue.export_csv()
        return report


def _cli() -> int:
    parser = argparse.ArgumentParser(description="BOOT rejected opportunity bridge")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--sibot1-db", action="append", default=[])
    parser.add_argument("--loop-seconds", type=float, default=0)
    args = parser.parse_args()
    bridge = RejectedOpportunityBridge(args.root)
    while True:
        print(json.dumps(bridge.run_once(args.sibot1_db), sort_keys=True))
        if args.loop_seconds <= 0:
            break
        time.sleep(max(1.0, args.loop_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
