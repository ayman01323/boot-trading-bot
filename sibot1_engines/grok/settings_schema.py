from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    chain: str = "solana"
    strategy_id: str = "CompactFlow-v1"
    trade_amount: Decimal = Decimal("1")
    min_confidence: Decimal = Decimal("0.55")
    min_volume_velocity: Decimal = Decimal("0.02")
    take_profit_pct: Decimal = Decimal("0.035")
    stop_loss_pct: Decimal = Decimal("-0.018")
    reject_dev_selling: bool = True
    max_source_age_ms: int = 750


def load_settings(path: str | Path) -> Settings:
    p=Path(path)
    if not p.exists(): return Settings()
    with p.open(newline="",encoding="utf-8") as f: row=next(csv.DictReader(f),None)
    if not row: return Settings()
    return Settings(
        chain=str(row.get("chain") or "solana").lower(), strategy_id=str(row.get("strategy_id") or "CompactFlow-v1"),
        trade_amount=Decimal(str(row.get("trade_amount") or "1")), min_confidence=Decimal(str(row.get("min_confidence") or "0.55")),
        min_volume_velocity=Decimal(str(row.get("min_volume_velocity") or "0.02")), take_profit_pct=Decimal(str(row.get("take_profit_pct") or "0.035")),
        stop_loss_pct=Decimal(str(row.get("stop_loss_pct") or "-0.018")), reject_dev_selling=str(row.get("reject_dev_selling") or "1").lower() in {"1","true","yes","on"},
        max_source_age_ms=int(row.get("max_source_age_ms") or 750),
    )
