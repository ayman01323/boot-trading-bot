from __future__ import annotations

import csv
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .cross_chain_strategy_signals import MarketFeatures


# Adapters translate existing bot evidence into the common Strategy Lab feature schema.
# They do not sign, submit, or execute transactions. Missing evidence is fail-closed:
# a missing liquidity/sellability/current-edge measurement becomes zero, not a guess.


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "pass", "ok"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _ratio_score(value: Any) -> Decimal:
    x = _d(value)
    if x > 1:
        x /= Decimal(100)
    return max(Decimal(0), min(Decimal(1), x))


def _bps(amount: Decimal, notional: Decimal) -> Decimal:
    if notional <= 0:
        return Decimal(0)
    return amount / notional * Decimal(10000)


def _source_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return prefix + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _route_ref(row: dict) -> str:
    route = str(row.get("route_path") or row.get("route_id") or "").strip()
    if not route:
        route = str(row.get("router_address") or "unknown-route")
    return "route_" + hashlib.sha256(route.lower().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AdaptedMarketFeature:
    source_type: str
    source_id: str
    features: MarketFeatures
    notional_base: Decimal = Decimal("0")
    gross_profit_base: Decimal = Decimal("0")
    fee_base: Decimal = Decimal("0")
    slippage_base: Decimal = Decimal("0")
    outcome_available: bool = False
    outcome_basis: str = "SIGNAL_ONLY"
    metadata: dict | None = None


def _learned_route_stats(app, chain_slug: str, route_path: str, current_notional: Decimal) -> tuple[Decimal, Decimal]:
    """Return (replicability 0..1, historical average net bps) for a matching route."""
    route_path = str(route_path or "").strip()
    if not route_path:
        return Decimal(0), Decimal(0)
    path = Path(app.data_dir) / f"{chain_slug}.sqlite3"
    if not path.exists():
        return Decimal(0), Decimal(0)
    try:
        conn = sqlite3.connect(path, timeout=2)
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_patterns'"
        ).fetchone()
        if not exists:
            conn.close()
            return Decimal(0), Decimal(0)
        row = conn.execute(
            """SELECT replicability,avg_net_base,confidence
                 FROM strategy_patterns
                WHERE COALESCE(route_fingerprint,'') LIKE ?
                  AND COALESCE(avg_net_base,0) > 0
                ORDER BY COALESCE(replicability,0) DESC, COALESCE(confidence,0) DESC,
                         COALESCE(tx_count,0) DESC
                LIMIT 1""",
            (f"%{route_path}%",),
        ).fetchone()
        conn.close()
        if not row:
            return Decimal(0), Decimal(0)
        rep = _ratio_score(row["replicability"])
        avg = _d(row["avg_net_base"])
        avg_bps = _bps(avg, current_notional) if current_notional > 0 else Decimal(0)
        return rep, avg_bps
    except Exception:
        return Decimal(0), Decimal(0)


def adapt_evm_opportunity(app, row: dict, *, now: int | None = None, source_type: str = "EVM_ROUTE") -> AdaptedMarketFeature:
    now = int(now or time.time())
    chain_slug = str(row.get("chain_slug") or row.get("chain") or "evm").strip().lower()
    observed = _int(row.get("observed_at_epoch") or row.get("timestamp_epoch") or row.get("updated_epoch"), 0)
    quote_age_ms = max(0, (now - observed) * 1000) if observed > 0 else 10**9
    notional = _d(row.get("source_input_base") or row.get("quote_input_base") or row.get("input_base"))
    gross_profit = _d(row.get("expected_gross_profit_base") or row.get("gross_profit_base"))
    gas = _d(row.get("estimated_gas_base") or row.get("gas_base"))
    builder = _d(row.get("builder_fee_base") or row.get("builder_payment_base"))
    slippage_base = _d(row.get("slippage_reserve_base") or row.get("slippage_base"))
    route_path = str(row.get("route_path") or "").strip()
    replicability, learned_avg_bps = _learned_route_stats(app, chain_slug, route_path, notional)

    liquidity_score = _ratio_score(row.get("liquidity_score"))
    if liquidity_score == 0 and _bool(row.get("liquidity_ok"), False):
        liquidity_score = Decimal(1)
    sellability_score = _ratio_score(row.get("sellability_score"))
    if sellability_score == 0 and _bool(row.get("sellability_ok"), False):
        sellability_score = Decimal(1)

    features = MarketFeatures(
        chain_type="EVM",
        chain_slug=chain_slug,
        asset=_route_ref(row),
        observed_at=observed or now,
        gross_edge_bps=_bps(gross_profit, notional),
        fees_bps=_bps(gas + builder, notional),
        slippage_bps=_bps(slippage_base, notional),
        price_impact_bps=_d(row.get("price_impact_bps")),
        latency_reserve_bps=_d(row.get("latency_reserve_bps")),
        liquidity_score=liquidity_score,
        sellability_score=sellability_score,
        holder_or_flow_dispersion=_ratio_score(row.get("holder_or_flow_dispersion") or row.get("flow_dispersion")),
        route_replicability=replicability or _ratio_score(row.get("route_replicability")),
        momentum_z=_d(row.get("momentum_z")),
        flow_acceleration_z=_d(row.get("flow_acceleration_z")),
        dislocation_z=_d(row.get("dislocation_z")),
        volatility_z=_d(row.get("volatility_z")),
        quote_age_ms=quote_age_ms,
        pool_age_seconds=_int(row.get("pool_age_seconds"), 0),
        independent_wallet_count=_int(row.get("independent_wallet_count"), 0),
        learned_route_avg_net_bps=learned_avg_bps or _d(row.get("learned_route_avg_net_bps")),
        forecast_positive_edge_probability=_ratio_score(row.get("forecast_positive_edge_probability")),
        forecast_expected_net_bps=_d(row.get("forecast_expected_net_bps")),
        forecast_uncertainty=_ratio_score(row.get("forecast_uncertainty")) if row.get("forecast_uncertainty") not in (None, "") else Decimal(1),
    )

    exact_quote = _bool(row.get("exact_quote_ok"), False)
    simulation = _bool(row.get("simulation_ok"), False)
    whole_route = _bool(row.get("whole_route_approved"), False) or _bool(row.get("route_approved"), False)
    executable = (
        notional > 0
        and exact_quote
        and simulation
        and _bool(row.get("liquidity_ok"), False)
        and _bool(row.get("sellability_ok"), False)
        and whole_route
    )
    sid = _source_id(
        "evm",
        chain_slug,
        row.get("route_id"),
        route_path,
        observed,
        row.get("router_address"),
        notional,
    )
    return AdaptedMarketFeature(
        source_type=source_type,
        source_id=sid,
        features=features,
        notional_base=notional,
        gross_profit_base=gross_profit,
        fee_base=gas + builder,
        slippage_base=slippage_base,
        outcome_available=bool(executable),
        outcome_basis="EXACT_QUOTE_AND_SIMULATION" if executable else "SIGNAL_ONLY",
        metadata={
            "route_ref": features.asset,
            "exact_quote_ok": exact_quote,
            "simulation_ok": simulation,
            "liquidity_ok": _bool(row.get("liquidity_ok"), False),
            "sellability_ok": _bool(row.get("sellability_ok"), False),
            "whole_route_approved": whole_route,
            "atomic_profit_protection": _bool(row.get("atomic_profit_protection"), False),
        },
    )


def load_evm_market_features(app, *, now: int | None = None, max_rows: int = 250) -> list[AdaptedMarketFeature]:
    now = int(now or time.time())
    paths = [
        Path(app.csv_dir) / "live_opportunities.csv",
        Path(app.csv_dir) / "auto" / "direct_market_opportunities.csv",
        Path(app.csv_dir) / "auto" / "learned_route_opportunities.csv",
    ]
    out: list[AdaptedMarketFeature] = []
    seen: set[str] = set()
    for path in paths:
        rows = _rows(path)
        for row in rows[-max(1, int(max_rows)) :]:
            item = adapt_evm_opportunity(app, row, now=now, source_type=f"EVM:{path.name}")
            # Ignore very old rows rather than repeatedly filling hourly windows with stale evidence.
            if item.features.quote_age_ms > 15 * 60 * 1000:
                continue
            if item.source_id in seen:
                continue
            seen.add(item.source_id)
            out.append(item)
    return out[-max(1, int(max_rows)) :]


def _solana_wallet_stats(conn: sqlite3.Connection, wallet: str) -> tuple[int, Decimal, Decimal]:
    """Historical context only; it is never converted into current executable edge."""
    try:
        rows = conn.execute(
            "SELECT cost_sol,net_sol FROM trades WHERE wallet=? ORDER BY sell_ts DESC LIMIT 100",
            (str(wallet),),
        ).fetchall()
    except Exception:
        return 0, Decimal(0), Decimal(1)
    wins = 0
    total = 0
    total_cost = Decimal(0)
    total_net = Decimal(0)
    for row in rows:
        cost = _d(row[0])
        net = _d(row[1])
        if cost <= 0:
            continue
        total += 1
        wins += int(net > 0)
        total_cost += cost
        total_net += net
    if not total or total_cost <= 0:
        return total, Decimal(0), Decimal(1)
    p = Decimal(wins) / Decimal(total)
    # Simple sample-size uncertainty indicator; not a trained forecast and not used as edge.
    uncertainty = max(Decimal("0.10"), min(Decimal(1), Decimal(1) / Decimal(total).sqrt()))
    return total, p, uncertainty


def load_solana_market_features(app, *, now: int | None = None, max_rows: int = 40) -> list[AdaptedMarketFeature]:
    """Adapt recent Solana leader-market observations without inventing current edge.

    Stored leader events do not contain a contemporaneous executable future-return quote.
    Therefore gross_edge_bps/liquidity/sellability remain fail-closed at zero here. The
    Strategy Lab still sees that Solana was scanned and records the evidence gap. A later
    quote/predictor adapter can populate the optional feature fields without changing the
    common strategy evaluators.
    """
    now = int(now or time.time())
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(path, timeout=2)
        conn.row_factory = sqlite3.Row
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leader_events'").fetchone()
        if not exists:
            conn.close()
            return []
        rows = conn.execute(
            """SELECT leader_wallet,signature,action,mint,token_amount_raw,sol_amount,event_ts
                 FROM leader_events
                WHERE action='BUY' AND event_ts>=?
                ORDER BY event_ts DESC LIMIT ?""",
            (now - 15 * 60, max(1, int(max_rows))),
        ).fetchall()
        out: list[AdaptedMarketFeature] = []
        for row in rows:
            sample, p_hist, uncertainty = _solana_wallet_stats(conn, str(row["leader_wallet"] or ""))
            event_ts = int(row["event_ts"] or now)
            features = MarketFeatures(
                chain_type="SOLANA",
                chain_slug="solana",
                asset="mint_" + hashlib.sha256(str(row["mint"] or "").encode("utf-8")).hexdigest()[:16],
                observed_at=event_ts,
                gross_edge_bps=Decimal(0),
                fees_bps=Decimal(0),
                slippage_bps=Decimal(0),
                price_impact_bps=Decimal(0),
                latency_reserve_bps=Decimal(0),
                liquidity_score=Decimal(0),
                sellability_score=Decimal(0),
                quote_age_ms=max(0, (now - event_ts) * 1000),
                independent_wallet_count=1,
                forecast_positive_edge_probability=p_hist,
                forecast_expected_net_bps=Decimal(0),
                forecast_uncertainty=uncertainty,
            )
            out.append(
                AdaptedMarketFeature(
                    source_type="SOLANA:leader_event",
                    source_id=_source_id("sol", row["signature"], row["mint"], event_ts),
                    features=features,
                    outcome_available=False,
                    outcome_basis="SIGNAL_ONLY_MISSING_CURRENT_EXECUTABLE_EDGE",
                    metadata={
                        "historical_wallet_sample": sample,
                        "historical_positive_ratio": str(p_hist),
                        "current_edge_proven": False,
                        "needs_quote_feature_adapter": True,
                    },
                )
            )
        conn.close()
        return out
    except Exception:
        return []


def load_market_features(app, *, now: int | None = None, evm_max_rows: int = 250, solana_max_rows: int = 40) -> list[AdaptedMarketFeature]:
    now = int(now or time.time())
    return [
        *load_evm_market_features(app, now=now, max_rows=evm_max_rows),
        *load_solana_market_features(app, now=now, max_rows=solana_max_rows),
    ]
