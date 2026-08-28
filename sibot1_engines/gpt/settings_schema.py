from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True, slots=True)
class Settings:
    # Existing Base atomic/cross-DEX research strategy.
    chain: str = "base"
    strategy_id: str = "gpt-netedge-arb-v1"
    trade_size_quote: Decimal = Decimal("5")
    min_net_edge_bps: Decimal = Decimal("12")
    max_quote_age_ms: int = 750
    max_open_ms: int = 15000
    emergency_loss_pct: Decimal = Decimal("0.8")

    # Independent Solana leader-quality strategy.  These are nomination gates only;
    # the protected Solana LIVE bridge still performs RugCheck/DexScreener,
    # forward+full-reverse+3x-reverse Jupiter validation and signed simulation.
    solana_enabled: bool = True
    solana_strategy_id: str = "gpt-leader-quality-v1"
    solana_trade_size_quote: Decimal = Decimal("1")
    solana_min_liquidity_usd: Decimal = Decimal("15000")
    solana_min_volume_usd: Decimal = Decimal("2500")
    solana_min_volume_liquidity_ratio: Decimal = Decimal("0.10")
    solana_min_confidence: Decimal = Decimal("0.70")
    solana_max_source_age_ms: int = 2500
    solana_max_leader_age_ms: int = 180000
    solana_min_liquidity_velocity_pct: Decimal = Decimal("-10")
    solana_min_lp_locked_pct: Decimal = Decimal("25")
    solana_require_dev_safe: bool = True
    solana_take_profit_pct: Decimal = Decimal("0.03")
    solana_stop_loss_pct: Decimal = Decimal("-0.015")
    solana_max_open_ms: int = 180000


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
        chain=values.get("chain", "base").lower(),
        strategy_id=values.get("strategy_id", "gpt-netedge-arb-v1"),
        trade_size_quote=Decimal(values.get("trade_size_quote", "5")),
        min_net_edge_bps=Decimal(values.get("min_net_edge_bps", "12")),
        max_quote_age_ms=int(values.get("max_quote_age_ms", "750")),
        max_open_ms=int(values.get("max_open_ms", "15000")),
        emergency_loss_pct=Decimal(values.get("emergency_loss_pct", "0.8")),
        solana_enabled=_bool(values.get("solana_enabled"), True),
        solana_strategy_id=values.get("solana_strategy_id", "gpt-leader-quality-v1"),
        solana_trade_size_quote=Decimal(values.get("solana_trade_size_quote", "1")),
        solana_min_liquidity_usd=Decimal(values.get("solana_min_liquidity_usd", "15000")),
        solana_min_volume_usd=Decimal(values.get("solana_min_volume_usd", "2500")),
        solana_min_volume_liquidity_ratio=Decimal(values.get("solana_min_volume_liquidity_ratio", "0.10")),
        solana_min_confidence=Decimal(values.get("solana_min_confidence", "0.70")),
        solana_max_source_age_ms=int(values.get("solana_max_source_age_ms", "2500")),
        solana_max_leader_age_ms=int(values.get("solana_max_leader_age_ms", "180000")),
        solana_min_liquidity_velocity_pct=Decimal(values.get("solana_min_liquidity_velocity_pct", "-10")),
        solana_min_lp_locked_pct=Decimal(values.get("solana_min_lp_locked_pct", "25")),
        solana_require_dev_safe=_bool(values.get("solana_require_dev_safe"), True),
        solana_take_profit_pct=Decimal(values.get("solana_take_profit_pct", "0.03")),
        solana_stop_loss_pct=Decimal(values.get("solana_stop_loss_pct", "-0.015")),
        solana_max_open_ms=int(values.get("solana_max_open_ms", "180000")),
    )
