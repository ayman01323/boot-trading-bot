from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(slots=True)
class EngineScore:
    engine_id: str
    chain: str
    signals: int = 0
    poolcheck_shadow: int = 0
    poolcheck_blocks: int = 0
    paper_entries: int = 0
    paper_exits: int = 0
    paper_wins: int = 0
    paper_losses: int = 0
    realised_pnl_quote: Decimal = Decimal("0")
    errors: int = 0
    last_event_epoch: int = 0


class Scoreboard:
    HEADERS = (
        "engine_id", "chain", "signals", "poolcheck_shadow", "poolcheck_blocks",
        "paper_entries", "paper_exits", "paper_wins", "paper_losses",
        "realised_pnl_quote", "errors", "last_event_epoch",
    )

    def __init__(self, runtime_dir: str | Path):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._scores: dict[tuple[str, str], EngineScore] = {}
        self._lock = RLock()
        self.audit_path = self.runtime_dir / "audit.ndjson"
        self.csv_path = self.runtime_dir / "scoreboard.csv"

    def _score(self, engine_id: str, chain: str) -> EngineScore:
        key = (str(engine_id), str(chain).lower())
        if key not in self._scores:
            self._scores[key] = EngineScore(key[0], key[1])
        return self._scores[key]

    def audit(self, event_type: str, **payload: Any) -> None:
        row = {"epoch_ms": int(time.time() * 1000), "event_type": str(event_type), **payload}
        with self._lock:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def signal(self, engine_id: str, chain: str, intent_id: str) -> None:
        with self._lock:
            score = self._score(engine_id, chain)
            score.signals += 1
            score.last_event_epoch = int(time.time())
        self.audit("SIGNAL", engine_id=engine_id, chain=chain, intent_id=intent_id)

    def poolcheck(self, engine_id: str, chain: str, verdict: str, reasons: tuple[str, ...]) -> None:
        verdict = str(verdict).upper()
        with self._lock:
            score = self._score(engine_id, chain)
            if verdict == "SHADOW_ONLY":
                score.poolcheck_shadow += 1
            if verdict in {"HARD_BLOCK", "COOLING"}:
                score.poolcheck_blocks += 1
            score.last_event_epoch = int(time.time())
        self.audit("POOLCHECK", engine_id=engine_id, chain=chain, verdict=verdict, reasons=list(reasons))

    def entry(self, engine_id: str, chain: str, *, tx_id: str, lot_id: str | None = None) -> None:
        with self._lock:
            score = self._score(engine_id, chain)
            score.paper_entries += 1
            score.last_event_epoch = int(time.time())
        self.audit("PAPER_ENTRY", engine_id=engine_id, chain=chain, tx_id=tx_id, lot_id=lot_id)

    def exit(self, engine_id: str, chain: str, *, tx_id: str, pnl: Decimal) -> None:
        pnl = Decimal(pnl)
        with self._lock:
            score = self._score(engine_id, chain)
            score.paper_exits += 1
            score.realised_pnl_quote += pnl
            score.paper_wins += int(pnl > 0)
            score.paper_losses += int(pnl < 0)
            score.last_event_epoch = int(time.time())
        self.audit("PAPER_EXIT", engine_id=engine_id, chain=chain, tx_id=tx_id, realised_pnl_quote=str(pnl))

    def error(self, engine_id: str, chain: str, detail: str) -> None:
        with self._lock:
            score = self._score(engine_id, chain)
            score.errors += 1
            score.last_event_epoch = int(time.time())
        self.audit("ERROR", engine_id=engine_id, chain=chain, detail=str(detail)[:500])

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for score in self._scores.values():
                row = asdict(score)
                row["realised_pnl_quote"] = str(score.realised_pnl_quote)
                rows.append(row)
            return sorted(rows, key=lambda row: (row["chain"], row["engine_id"]))

    def flush(self) -> None:
        rows = self.snapshot()
        tmp = self.csv_path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.HEADERS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.csv_path)
