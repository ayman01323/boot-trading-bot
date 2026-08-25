from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    engine_id: str = "gemini"
    engine_version: str = "1.1.0"
    strategy_id: str = "Gemini-PulseFlow"
    chain: str = "solana"
    trade_amount: Decimal = Decimal("1.5")
    min_liquidity_usd: Decimal = Decimal("10000")
    min_volume_usd: Decimal = Decimal("500")
    min_volume_liquidity_ratio: Decimal = Decimal("0.05")
    max_volume_liquidity_ratio: Decimal = Decimal("10")
    min_liquidity_velocity_pct: Decimal = Decimal("-20")
    min_lp_locked_pct_prefilter: Decimal = Decimal("50")
    signal_cooldown_ms: int = 15 * 60 * 1000
    take_profit_pct: Decimal = Decimal("0.05")
    max_source_age_ms: int = 750


def load_settings(path: str | Path) -> Settings:
    p = Path(path)
    if not p.exists():
        return Settings()
    with p.open(newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f), None)
    if not row:
        return Settings()
    return Settings(
        engine_id=str(row.get("engine_id") or "gemini"),
        engine_version=str(row.get("engine_version") or "1.1.0"),
        strategy_id=str(row.get("strategy_id") or "Gemini-PulseFlow"),
        chain=str(row.get("chain") or "solana").lower(),
        trade_amount=Decimal(str(row.get("trade_amount") or "1.5")),
        min_liquidity_usd=Decimal(str(row.get("min_liquidity_usd") or "10000")),
        min_volume_usd=Decimal(str(row.get("min_volume_usd") or "500")),
        min_volume_liquidity_ratio=Decimal(str(row.get("min_volume_liquidity_ratio") or "0.05")),
        max_volume_liquidity_ratio=Decimal(str(row.get("max_volume_liquidity_ratio") or "10")),
        min_liquidity_velocity_pct=Decimal(str(row.get("min_liquidity_velocity_pct") or "-20")),
        min_lp_locked_pct_prefilter=Decimal(str(row.get("min_lp_locked_pct_prefilter") or "50")),
        signal_cooldown_ms=int(row.get("signal_cooldown_ms") or 15 * 60 * 1000),
        take_profit_pct=Decimal(str(row.get("take_profit_pct") or "0.05")),
        max_source_age_ms=int(row.get("max_source_age_ms") or 750),
    )
