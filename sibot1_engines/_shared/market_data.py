from __future__ import annotations

import csv
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .contracts import MarketEvent


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _b(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "pass", "ok"}


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _event_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return prefix + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class MarketEvidenceBook:
    """Read-only evidence attached to normalized market events.

    Engines receive only MarketEvent. Central PoolCheck receives the original
    safety/executability evidence through this book, keyed by market_event_id.
    """

    def __init__(self, max_events: int = 4096):
        self.max_events = max(128, int(max_events))
        self._items: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = RLock()

    def put(self, event_id: str, evidence: dict[str, Any]) -> None:
        with self._lock:
            if event_id not in self._items:
                self._order.append(event_id)
            self._items[event_id] = dict(evidence)
            while len(self._order) > self.max_events:
                old = self._order.pop(0)
                self._items.pop(old, None)

    def get(self, event_id: str | None) -> dict[str, Any]:
        if not event_id:
            return {}
        with self._lock:
            return dict(self._items.get(str(event_id), {}))


class EvmOpportunityCsvSource:
    """Convert MAIN BOOT's fresh read-only opportunity CSVs to SiBot1 events.

    A GPT `dex_spread` event is emitted only when the upstream venue plan proves
    at least two distinct venues. Single-venue routes remain visible as
    `evm_route` events but cannot be misrepresented as cross-DEX arbitrage.
    """

    def __init__(self, csv_dir: str | Path, evidence: MarketEvidenceBook, max_age_seconds: int = 900):
        self.csv_dir = Path(csv_dir)
        self.evidence = evidence
        self.max_age_seconds = max(1, int(max_age_seconds))
        self._seen: set[str] = set()
        self.paths = (
            self.csv_dir / "live_opportunities.csv",
            self.csv_dir / "auto" / "direct_market_opportunities.csv",
            self.csv_dir / "auto" / "learned_route_opportunities.csv",
        )

    @staticmethod
    def _venues(row: dict[str, str]) -> tuple[str, ...]:
        raw = str(row.get("venue_plan") or row.get("protocol") or "").strip()
        if not raw:
            return ()
        for sep in (">", "|", ";", ","):
            raw = raw.replace(sep, "\n")
        out: list[str] = []
        for item in raw.splitlines():
            value = item.strip()
            if value and value.lower() not in {x.lower() for x in out}:
                out.append(value)
        return tuple(out)

    def poll(self, now_epoch: int | None = None) -> list[MarketEvent]:
        now = int(now_epoch or time.time())
        out: list[MarketEvent] = []
        for path in self.paths:
            for row in _rows(path)[-250:]:
                observed = _i(row.get("observed_at_epoch"), 0)
                if observed <= 0 or now - observed > self.max_age_seconds:
                    continue
                route = tuple(x.strip() for x in str(row.get("route_path") or "").split(">") if x.strip())
                if len(route) < 2:
                    continue
                eid = _event_id("evm", path.name, row.get("chain_slug"), row.get("route_id"), row.get("route_path"), observed)
                if eid in self._seen:
                    continue
                self._seen.add(eid)
                notional = _d(row.get("source_input_base") or row.get("quote_input_base"))
                gross = _d(row.get("expected_gross_profit_base"))
                gas = _d(row.get("estimated_gas_base"))
                builder = _d(row.get("builder_fee_base"))
                slippage = _d(row.get("slippage_reserve_base"))
                gross_bps = gross / notional * Decimal("10000") if notional > 0 else Decimal("0")
                cost_bps = (gas + builder + slippage) / notional * Decimal("10000") if notional > 0 else Decimal("0")
                venues = self._venues(row)
                event_type = "dex_spread" if len(venues) >= 2 else "evm_route"
                quoted_output = _d(row.get("quoted_output_base"))
                price = quoted_output / notional if quoted_output > 0 and notional > 0 else None
                checks = {
                    "exact_quote_ok": _b(row.get("exact_quote_ok")),
                    "simulation_ok": _b(row.get("simulation_ok")),
                    "liquidity_ok": _b(row.get("liquidity_ok")),
                    "sellability_ok": _b(row.get("sellability_ok")),
                    "route_approved": _b(row.get("route_approved")),
                    "whole_route_approved": _b(row.get("whole_route_approved")),
                    "atomic_profit_protection": _b(row.get("atomic_profit_protection")),
                    "price_impact_bps": str(row.get("price_impact_bps") or ""),
                    "source_path": str(path),
                    "route_path": tuple(route),
                    "venue_plan": venues,
                }
                self.evidence.put(eid, checks)
                out.append(MarketEvent(
                    event_id=eid,
                    chain=str(row.get("chain_slug") or "evm").strip().lower(),
                    observed_at_ms=observed * 1000,
                    source=f"boot:{path.name}",
                    event_type=event_type,
                    asset_in=route[0],
                    asset_out=route[1],
                    price=price,
                    source_age_ms=max(0, (now - observed) * 1000),
                    payload={
                        "gross_edge_bps": str(gross_bps),
                        "estimated_cost_bps": str(cost_bps),
                        "quote_age_ms": max(0, (now - observed) * 1000),
                        "buy_venue": venues[0] if len(venues) >= 2 else "",
                        "sell_venue": venues[1] if len(venues) >= 2 else "",
                        "route_path": tuple(route),
                        "venue_plan": venues,
                        **checks,
                    },
                ))
        if len(self._seen) > 20000:
            self._seen = {event.event_id for event in out[-1000:]}
        return out


@dataclass(slots=True)
class _SolSnapshot:
    at: float
    volume: Decimal
    liquidity: Decimal


class SolanaLeaderDexSource:
    """Low-cost Solana market enrichment for recent MAIN BOOT leader BUY mints.

    It reuses the existing PoolCheck DexScreener cache, so a later PoolCheck call
    for the same mint does not multiply provider load. No signing or transaction
    endpoint is used.
    """

    def __init__(self, data_dir: str | Path, evidence: MarketEvidenceBook, *, max_mints: int = 12, cache_seconds: int = 30):
        self.data_dir = Path(data_dir)
        self.evidence = evidence
        self.max_mints = max(1, min(50, int(max_mints)))
        self.cache_seconds = max(15, int(cache_seconds))
        self._seen_event: dict[str, int] = {}
        self._prior: dict[str, _SolSnapshot] = {}

    def _recent_mints(self, now: int) -> list[tuple[str, str, int]]:
        db = self.data_dir / "solana_sibot.sqlite3"
        if not db.exists():
            return []
        try:
            conn = sqlite3.connect(db, timeout=2)
            rows = conn.execute(
                """SELECT mint,signature,event_ts FROM leader_events
                   WHERE action='BUY' AND event_ts>=? AND mint IS NOT NULL AND mint!=''
                   ORDER BY event_ts DESC LIMIT ?""",
                (now - 15 * 60, self.max_mints * 4),
            ).fetchall()
            conn.close()
        except Exception:
            return []
        out: list[tuple[str, str, int]] = []
        seen: set[str] = set()
        for mint, signature, event_ts in rows:
            mint = str(mint or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            out.append((mint, str(signature or ""), int(event_ts or now)))
            if len(out) >= self.max_mints:
                break
        return out

    @staticmethod
    def _liq(pair: dict[str, Any]) -> Decimal:
        return max(Decimal("0"), _d((pair.get("liquidity") or {}).get("usd")))

    def poll(self, now_epoch: int | None = None) -> list[MarketEvent]:
        now = int(now_epoch or time.time())
        try:
            from learnerbot import solana_pool_risk_gate as pool_gate
        except Exception:
            return []
        events: list[MarketEvent] = []
        for mint, signature, leader_ts in self._recent_mints(now):
            try:
                pairs, cached = pool_gate._fetch_json(
                    "dexscreener",
                    pool_gate._DEX_URL.format(mint=mint),
                    mint,
                    float(self.cache_seconds),
                    2.5,
                )
            except Exception:
                continue
            if not isinstance(pairs, list):
                continue
            pairs = [p for p in pairs if isinstance(p, dict) and str(p.get("chainId") or "solana").lower() == "solana"]
            base_pairs = [p for p in pairs if str((p.get("baseToken") or {}).get("address") or "") == mint]
            candidates = base_pairs or pairs
            if not candidates:
                continue
            best = max(candidates, key=self._liq)
            price_native = _d(best.get("priceNative"))
            if price_native <= 0:
                continue
            liquidity = sum((self._liq(p) for p in pairs), Decimal("0"))
            volume = sum((max(Decimal("0"), _d((p.get("volume") or {}).get("h24"))) for p in pairs), Decimal("0"))
            prior = self._prior.get(mint)
            volume_velocity = Decimal("0")
            liquidity_velocity = Decimal("0")
            if prior and now > prior.at:
                volume_velocity = max(Decimal("0"), (volume - prior.volume) / max(Decimal("1"), prior.volume) * Decimal("100"))
                liquidity_velocity = (liquidity - prior.liquidity) / max(Decimal("1"), prior.liquidity) * Decimal("100")
            self._prior[mint] = _SolSnapshot(float(now), volume, liquidity)
            pair_count = len(pairs)
            confidence = Decimal("0.55")
            if liquidity >= Decimal("10000"):
                confidence += Decimal("0.10")
            if volume >= Decimal("500"):
                confidence += Decimal("0.10")
            if pair_count >= 2:
                confidence += Decimal("0.05")
            if not cached:
                confidence += Decimal("0.05")
            confidence = min(Decimal("0.90"), confidence)
            eid = _event_id("sol", signature, mint, now // self.cache_seconds)
            if self._seen_event.get(mint) == now // self.cache_seconds:
                continue
            self._seen_event[mint] = now // self.cache_seconds
            quote_address = str((best.get("quoteToken") or {}).get("address") or "")
            evidence = {
                "mint": mint,
                "leader_signature": signature,
                "leader_event_ts": leader_ts,
                "dex_pair_count": pair_count,
                "dex_liquidity_usd_total": str(liquidity),
                "dex_volume_h24_usd": str(volume),
                "dex_id": str(best.get("dexId") or ""),
                "pair_address": str(best.get("pairAddress") or ""),
                "dexscreener_cached": bool(cached),
                "full_reverse_sellability_proven": False,
                "stress_exit_3x_proven": False,
            }
            self.evidence.put(eid, evidence)
            events.append(MarketEvent(
                event_id=eid,
                chain="solana",
                observed_at_ms=now * 1000,
                source="boot:solana-leader+dexscreener-cache",
                event_type="market_pulse",
                asset_in=quote_address or "SOL",
                asset_out=mint,
                pool_id=str(best.get("pairAddress") or "") or None,
                price=price_native,
                liquidity_usd=liquidity,
                volume_usd=volume,
                source_age_ms=0,
                payload={
                    "venue": str(best.get("dexId") or ""),
                    "volume_velocity": str(volume_velocity),
                    "liquidity_velocity": str(liquidity_velocity),
                    "confidence": str(confidence),
                    "confidence_basis": "market_data_completeness_not_ai_forecast",
                    "dev_selling_known": False,
                    "dev_selling": False,
                    "leader_event_age_ms": max(0, (now - leader_ts) * 1000),
                },
            ))
        return events


class SharedBootMarketSource:
    def __init__(self, csv_dir: str | Path, data_dir: str | Path, evidence: MarketEvidenceBook):
        self.evm = EvmOpportunityCsvSource(csv_dir, evidence)
        self.solana = SolanaLeaderDexSource(data_dir, evidence)

    def poll(self) -> list[MarketEvent]:
        return [*self.evm.poll(), *self.solana.poll()]
