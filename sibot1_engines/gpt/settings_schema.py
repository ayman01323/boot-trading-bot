from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    chain: str = "base"
    strategy_id: str = "gpt-netedge-arb-v1"
    trade_size_quote: Decimal = Decimal("5")
    min_net_edge_bps: Decimal = Decimal("12")
    max_quote_age_ms: int = 750
    max_open_ms: int = 15000
    emergency_loss_pct: Decimal = Decimal("0.8")


def load_settings(path: str | Path) -> Settings:
    p = Path(path)
    if not p.exists():
        return Settings()
    values: dict[str, str] = {}
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = str(row.get("key") or "").strip()
            if key:
                values[key] = str(row.get("value") or "").strip()
    return Settings(
        chain=values.get("chain", "base"),
        strategy_id=values.get("strategy_id", "gpt-netedge-arb-v1"),
        trade_size_quote=Decimal(values.get("trade_size_quote", "5")),
        min_net_edge_bps=Decimal(values.get("min_net_edge_bps", "12")),
        max_quote_age_ms=int(values.get("max_quote_age_ms", "750")),
        max_open_ms=int(values.get("max_open_ms", "15000")),
        emergency_loss_pct=Decimal(values.get("emergency_loss_pct", "0.8")),
    )
