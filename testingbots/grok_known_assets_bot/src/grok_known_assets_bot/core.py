from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLACEHOLDER_PREFIXES = ("REPLACE_", "ADDRESS_REQUIRED", "MINT_REQUIRED", "<")


@dataclass(frozen=True)
class Asset:
    key: str
    chain: str
    symbol: str
    address: str
    enabled: bool = True

    @property
    def is_native(self) -> bool:
        return self.address == "NATIVE"


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.35
    max_gross_position_pct: float = 2.0
    max_concurrent_positions: int = 2
    max_chain_exposure_pct: float = 3.0
    daily_realised_loss_pct: float = 2.0
    consecutive_loss_limit: int = 3
    max_quote_age_s: float = 20.0
    max_spread_bps: float = 80.0
    max_price_impact_bps: float = 100.0
    min_liquidity_usd: float = 250_000.0
    min_volume_5m_usd: float = 25_000.0
    max_liquidity_fraction_pct: float = 0.10
    stop_min_pct: float = 2.5
    stop_max_pct: float = 4.0
    take_profit_1_pct: float = 2.0
    take_profit_2_pct: float = 4.0
    trailing_drawdown_pct: float = 1.0
    max_hold_minutes: float = 60.0
    cooldown_minutes: float = 20.0
    min_net_edge_pct: float = 0.60
    kill_switch: bool = False


@dataclass(frozen=True)
class MarketSnapshot:
    asset_key: str
    ts: float
    bid: float
    ask: float
    reverse_bid: float
    liquidity_usd: float
    volume_5m_usd: float
    ret_1m_pct: float
    ret_5m_pct: float
    ret_15m_pct: float
    vol_5m_pct: float
    spread_bps: float
    price_impact_bps: float
    fee_bps: float
    sellable: bool = True
    slippage_bps: float = 0.0

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "MarketSnapshot":
        return cls(
            asset_key=str(row["asset_key"]),
            ts=float(row["ts"]),
            bid=float(row["bid"]),
            ask=float(row["ask"]),
            reverse_bid=float(row.get("reverse_bid", row["bid"])),
            liquidity_usd=float(row["liquidity_usd"]),
            volume_5m_usd=float(row["volume_5m_usd"]),
            ret_1m_pct=float(row["ret_1m_pct"]),
            ret_5m_pct=float(row["ret_5m_pct"]),
            ret_15m_pct=float(row["ret_15m_pct"]),
            vol_5m_pct=float(row["vol_5m_pct"]),
            spread_bps=float(row["spread_bps"]),
            price_impact_bps=float(row["price_impact_bps"]),
            fee_bps=float(row.get("fee_bps", 0.0)),
            sellable=bool(row.get("sellable", True)),
            slippage_bps=float(row.get("slippage_bps", 0.0)),
        )


@dataclass
class Position:
    asset_key: str
    chain: str
    opened_ts: float
    entry_price: float
    quantity: float
    remaining_quantity: float
    stop_pct: float
    peak_net_pct: float = -999.0
    took_tp1: bool = False
    trade_id: str = ""


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    size_usd: float = 0.0
    stop_pct: float = 0.0
    net_edge_pct: float = 0.0
    exit_fraction: float = 0.0


class Journal:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
            asset_key TEXT, payload TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS state(
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL)""")
        self.db.commit()

    def event(self, kind: str, asset_key: str | None, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events(ts, kind, asset_key, payload) VALUES(?,?,?,?)",
            (time.time(), kind, asset_key, json.dumps(payload, sort_keys=True)),
        )
        self.db.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_state(self, key: str, value: Any) -> None:
        self.db.execute(
            """INSERT INTO state(key, value, updated_ts) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts""",
            (key, json.dumps(value, sort_keys=True), time.time()),
        )
        self.db.commit()

    def realised_pnl_today(self, day_start_ts: float) -> float:
        rows = self.db.execute(
            "SELECT payload FROM events WHERE kind='CLOSE' AND ts>=?", (day_start_ts,)
        ).fetchall()
        return sum(float(json.loads(payload).get("realised_pnl_usd", 0.0)) for (payload,) in rows)

    def consecutive_losses(self) -> int:
        """Count completed losing trades, never individual partial CLOSE events."""
        rows = self.db.execute(
            "SELECT payload FROM events WHERE kind='TRADE_RESULT' ORDER BY id DESC LIMIT 50"
        ).fetchall()
        count = 0
        for (payload,) in rows:
            pnl = float(json.loads(payload).get("realised_pnl_usd", 0.0))
            if pnl < 0.0:
                count += 1
            else:
                break
        return count

    def day_start_equity(self, now: float, current_equity: float) -> float:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        state_key = f"day_start_equity:{day_key}"
        stored = self.get_state(state_key)
        if stored is None:
            self.set_state(state_key, float(current_equity))
            return float(current_equity)
        return float(stored)

    def accumulate_trade_pnl(self, trade_id: str, pnl_usd: float, *, final: bool, asset_key: str) -> float:
        state_key = f"trade_pnl:{trade_id}"
        total = float(self.get_state(state_key, 0.0)) + float(pnl_usd)
        if final:
            self.db.execute("DELETE FROM state WHERE key=?", (state_key,))
            self.db.commit()
            self.event("TRADE_RESULT", asset_key, {"trade_id": trade_id, "realised_pnl_usd": total})
        else:
            self.set_state(state_key, total)
        return total

    def recent_stop_ts(self, asset_key: str) -> float | None:
        rows = self.db.execute(
            "SELECT ts, payload FROM events WHERE kind='CLOSE' AND asset_key=? ORDER BY id DESC LIMIT 20",
            (asset_key,),
        ).fetchall()
        for ts, payload in rows:
            if json.loads(payload).get("reason") == "HARD_STOP":
                return float(ts)
        return None

    def report(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT kind, payload FROM events ORDER BY id").fetchall()
        realised = 0.0
        closed_trades = 0
        wins = 0
        rejects = 0
        close_events = 0
        for kind, payload in rows:
            data = json.loads(payload)
            if kind == "CLOSE":
                close_events += 1
                realised += float(data.get("realised_pnl_usd", 0.0))
            elif kind == "TRADE_RESULT":
                closed_trades += 1
                wins += int(float(data.get("realised_pnl_usd", 0.0)) > 0.0)
            elif kind == "REJECT":
                rejects += 1
        return {
            "events": len(rows),
            "close_events": close_events,
            "closed_trades": closed_trades,
            "wins": wins,
            "win_rate_pct": (wins / closed_trades * 100.0) if closed_trades else 0.0,
            "realised_pnl_usd": realised,
            "rejects": rejects,
        }


def _is_placeholder(address: str) -> bool:
    value = address.strip().upper()
    return any(value.startswith(p) for p in PLACEHOLDER_PREFIXES)


def load_config(path: str | Path) -> tuple[dict[str, Asset], RiskConfig, dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assets: dict[str, Asset] = {}
    identity_pairs: set[tuple[str, str]] = set()
    for row in raw.get("assets", []):
        asset = Asset(
            key=str(row["key"]).strip(),
            chain=str(row["chain"]).strip().lower(),
            symbol=str(row["symbol"]).strip().upper(),
            address=str(row["address"]).strip(),
            enabled=bool(row.get("enabled", True)),
        )
        if not asset.key or not asset.chain or not asset.symbol or not asset.address:
            raise ValueError("asset fields may not be empty")
        if asset.key in assets:
            raise ValueError(f"duplicate asset key: {asset.key}")
        identity = (asset.chain, asset.address)
        if identity in identity_pairs:
            raise ValueError(f"duplicate chain/address: {identity}")
        identity_pairs.add(identity)
        if asset.enabled and not asset.is_native and _is_placeholder(asset.address):
            raise ValueError(f"enabled asset has placeholder address: {asset.key}")
        assets[asset.key] = asset
    if not assets:
        raise ValueError("asset allow-list is empty")
    r = raw.get("risk", {})
    risk = RiskConfig(**{k: v for k, v in r.items() if k in RiskConfig.__dataclass_fields__})
    _validate_risk(risk)
    return assets, risk, raw


def _validate_risk(r: RiskConfig) -> None:
    if not 0 < r.risk_per_trade_pct <= 1.0:
        raise ValueError("risk_per_trade_pct must be >0 and <=1")
    if not 0 < r.max_gross_position_pct <= 10:
        raise ValueError("max_gross_position_pct out of bounds")
    if r.stop_min_pct <= 0 or r.stop_max_pct < r.stop_min_pct:
        raise ValueError("invalid stop bounds")
    if r.take_profit_1_pct <= 0 or r.take_profit_2_pct <= r.take_profit_1_pct:
        raise ValueError("invalid profit targets")
    if r.max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be >=1")
    if r.max_quote_age_s <= 0:
        raise ValueError("max_quote_age_s must be >0")
    if min(r.max_spread_bps, r.max_price_impact_bps, r.min_net_edge_pct) < 0:
        raise ValueError("risk units may not be negative")


class StrategyEngine:
    def __init__(self, assets: dict[str, Asset], risk: RiskConfig, journal: Journal):
        self.assets = assets
        self.risk = risk
        self.journal = journal
        self.positions: dict[str, Position] = {}
        self.start_of_day_equity: float | None = None

    def _asset(self, key: str) -> Asset:
        asset = self.assets.get(key)
        if asset is None or not asset.enabled:
            raise PermissionError(f"asset not enabled in allow-list: {key}")
        return asset

    def _day_start(self, now: float) -> float:
        return now - (now % 86400)

    def breakers_ok(self, equity: float, now: float) -> tuple[bool, str]:
        if self.risk.kill_switch:
            return False, "KILL_SWITCH"
        if self.start_of_day_equity is None:
            self.start_of_day_equity = self.journal.day_start_equity(now, equity)
        realised = self.journal.realised_pnl_today(self._day_start(now))
        loss_limit = -self.start_of_day_equity * self.risk.daily_realised_loss_pct / 100.0
        if realised <= loss_limit:
            return False, "DAILY_LOSS_BREAKER"
        if self.journal.consecutive_losses() >= self.risk.consecutive_loss_limit:
            return False, "CONSECUTIVE_LOSS_BREAKER"
        return True, "OK"

    def _quote_checks(self, snap: MarketSnapshot, now: float) -> list[str]:
        reasons: list[str] = []
        if max(0.0, now - snap.ts) > self.risk.max_quote_age_s:
            reasons.append("STALE_QUOTE")
        if not snap.sellable or snap.reverse_bid <= 0:
            reasons.append("NO_SELL_PATH")
        if snap.ask <= 0 or snap.bid <= 0 or snap.ask < snap.bid:
            reasons.append("INVALID_MARKET")
        if snap.liquidity_usd < self.risk.min_liquidity_usd:
            reasons.append("LOW_LIQUIDITY")
        if snap.volume_5m_usd < self.risk.min_volume_5m_usd:
            reasons.append("LOW_VOLUME")
        if snap.spread_bps < 0 or snap.spread_bps > self.risk.max_spread_bps:
            reasons.append("WIDE_OR_INVALID_SPREAD")
        if snap.price_impact_bps < 0 or snap.price_impact_bps > self.risk.max_price_impact_bps:
            reasons.append("HIGH_OR_INVALID_PRICE_IMPACT")
        if snap.fee_bps < 0 or snap.slippage_bps < 0:
            reasons.append("INVALID_NEGATIVE_COST")
        return reasons

    def _stop_pct(self, snap: MarketSnapshot) -> float:
        return min(self.risk.stop_max_pct, max(self.risk.stop_min_pct, 1.5 * abs(snap.vol_5m_pct)))

    def _estimated_round_trip_cost_pct(self, snap: MarketSnapshot) -> float:
        return (
            snap.spread_bps
            + 2.0 * snap.fee_bps
            + 2.0 * snap.price_impact_bps
            + 2.0 * snap.slippage_bps
        ) / 100.0

    def _momentum_ok(self, snap: MarketSnapshot) -> tuple[bool, str]:
        if snap.ret_15m_pct <= 0:
            return False, "NO_15M_TREND"
        if snap.ret_5m_pct < 0.30:
            return False, "WEAK_5M_MOMENTUM"
        if snap.ret_5m_pct > 5.0:
            return False, "OVEREXTENDED_5M"
        if snap.ret_1m_pct < -0.50:
            return False, "ADVERSE_1M_REVERSAL"
        return True, "OK"

    def _chain_exposure_usd(self, chain: str) -> float:
        return sum(
            p.remaining_quantity * p.entry_price
            for p in self.positions.values()
            if p.chain == chain
        )

    def size_position(self, equity: float, stop_pct: float, snap: MarketSnapshot, chain: str) -> float:
        risk_budget = equity * self.risk.risk_per_trade_pct / 100.0
        by_stop = risk_budget / (stop_pct / 100.0)
        gross_cap = equity * self.risk.max_gross_position_pct / 100.0
        chain_cap = equity * self.risk.max_chain_exposure_pct / 100.0
        chain_room = max(0.0, chain_cap - self._chain_exposure_usd(chain))
        liquidity_cap = snap.liquidity_usd * self.risk.max_liquidity_fraction_pct / 100.0
        return max(0.0, min(by_stop, gross_cap, chain_room, liquidity_cap))

    def evaluate_entry(self, snap: MarketSnapshot, equity: float, now: float | None = None) -> Decision:
        now = float(now if now is not None else time.time())
        try:
            asset = self._asset(snap.asset_key)
        except PermissionError as exc:
            self.journal.event("REJECT", snap.asset_key, {"reason": "UNLISTED_OR_DISABLED"})
            return Decision("REJECT", str(exc))
        ok, reason = self.breakers_ok(equity, now)
        if not ok:
            self.journal.event("REJECT", snap.asset_key, {"reason": reason})
            return Decision("REJECT", reason)
        if snap.asset_key in self.positions:
            return Decision("HOLD", "POSITION_ALREADY_OPEN")
        if len(self.positions) >= self.risk.max_concurrent_positions:
            return Decision("REJECT", "MAX_CONCURRENT_POSITIONS")
        last_stop = self.journal.recent_stop_ts(snap.asset_key)
        if last_stop and now - last_stop < self.risk.cooldown_minutes * 60:
            return Decision("REJECT", "STOP_COOLDOWN")
        reasons = self._quote_checks(snap, now)
        if reasons:
            reason = "|".join(reasons)
            self.journal.event("REJECT", snap.asset_key, {"reason": reason})
            return Decision("REJECT", reason)
        momentum_ok, reason = self._momentum_ok(snap)
        if not momentum_ok:
            self.journal.event("REJECT", snap.asset_key, {"reason": reason})
            return Decision("REJECT", reason)
        net_edge = self.risk.take_profit_1_pct - self._estimated_round_trip_cost_pct(snap)
        if net_edge < self.risk.min_net_edge_pct:
            self.journal.event(
                "REJECT",
                snap.asset_key,
                {"reason": "INSUFFICIENT_EDGE_AFTER_COST", "net_edge_pct": net_edge},
            )
            return Decision("REJECT", "INSUFFICIENT_EDGE_AFTER_COST", net_edge_pct=net_edge)
        stop_pct = self._stop_pct(snap)
        size_usd = self.size_position(equity, stop_pct, snap, asset.chain)
        if size_usd <= 0:
            return Decision("REJECT", "NO_RISK_CAPACITY", stop_pct=stop_pct, net_edge_pct=net_edge)
        return Decision(
            "ENTER",
            "MOMENTUM_CONFIRMED",
            size_usd=size_usd,
            stop_pct=stop_pct,
            net_edge_pct=net_edge,
        )

    def open_paper(self, snap: MarketSnapshot, decision: Decision) -> Position:
        if decision.action != "ENTER":
            raise ValueError("decision is not ENTER")
        asset = self._asset(snap.asset_key)
        qty = decision.size_usd / snap.ask
        trade_id = uuid.uuid4().hex
        p = Position(
            snap.asset_key,
            asset.chain,
            snap.ts,
            snap.ask,
            qty,
            qty,
            decision.stop_pct,
            trade_id=trade_id,
        )
        self.positions[p.asset_key] = p
        self.journal.event(
            "OPEN",
            p.asset_key,
            {
                "trade_id": trade_id,
                "entry_price": p.entry_price,
                "quantity": qty,
                "size_usd": decision.size_usd,
                "paper": True,
            },
        )
        return p

    def net_return_pct(self, p: Position, snap: MarketSnapshot) -> float:
        gross = (snap.reverse_bid / p.entry_price - 1.0) * 100.0
        return gross - (snap.fee_bps + snap.price_impact_bps + snap.slippage_bps) / 100.0

    def evaluate_exit(self, p: Position, snap: MarketSnapshot, now: float | None = None) -> Decision:
        now = float(now if now is not None else snap.ts)
        reasons = self._quote_checks(snap, now)
        net = self.net_return_pct(p, snap)
        p.peak_net_pct = max(p.peak_net_pct, net)
        if "NO_SELL_PATH" in reasons:
            return Decision("EMERGENCY", "NO_SELL_PATH", exit_fraction=1.0)
        if "STALE_QUOTE" in reasons:
            return Decision("HOLD", "STALE_EXIT_QUOTE")
        if net <= -p.stop_pct:
            return Decision("EXIT", "HARD_STOP", exit_fraction=1.0)
        if snap.liquidity_usd < self.risk.min_liquidity_usd * 0.70:
            return Decision("EXIT", "LIQUIDITY_DETERIORATION", exit_fraction=1.0)
        if snap.spread_bps > self.risk.max_spread_bps * 1.5:
            return Decision("EXIT", "SPREAD_DETERIORATION", exit_fraction=1.0)
        if max(0.0, now - p.opened_ts) / 60.0 >= self.risk.max_hold_minutes:
            return Decision("EXIT", "TIME_STOP", exit_fraction=1.0)
        if snap.ret_1m_pct <= -0.70 and net > -p.stop_pct:
            return Decision("EXIT", "MOMENTUM_REVERSAL", exit_fraction=1.0)
        if net >= self.risk.take_profit_2_pct:
            return Decision("EXIT", "TAKE_PROFIT_2", exit_fraction=1.0)
        if net >= self.risk.take_profit_1_pct and not p.took_tp1:
            return Decision("EXIT", "TAKE_PROFIT_1", exit_fraction=0.50)
        if (
            p.took_tp1
            and p.peak_net_pct >= self.risk.take_profit_1_pct
            and p.peak_net_pct - net >= self.risk.trailing_drawdown_pct
        ):
            return Decision("EXIT", "TRAILING_EXIT", exit_fraction=1.0)
        return Decision("HOLD", "NO_EXIT")

    def close_paper(self, p: Position, snap: MarketSnapshot, decision: Decision) -> float:
        if decision.action not in {"EXIT", "EMERGENCY"}:
            raise ValueError("decision is not an exit")
        frac = min(1.0, max(0.0, decision.exit_fraction))
        qty = p.remaining_quantity * frac
        proceeds = qty * max(0.0, snap.reverse_bid)
        cost_basis = qty * p.entry_price
        exit_cost = proceeds * (snap.fee_bps + snap.price_impact_bps + snap.slippage_bps) / 10_000.0
        pnl = proceeds - exit_cost - cost_basis
        p.remaining_quantity -= qty
        if decision.reason == "TAKE_PROFIT_1":
            p.took_tp1 = True
        final = p.remaining_quantity <= p.quantity * 1e-9 or frac >= 0.999
        if final:
            self.positions.pop(p.asset_key, None)
        self.journal.event(
            "CLOSE",
            p.asset_key,
            {
                "trade_id": p.trade_id,
                "reason": decision.reason,
                "quantity": qty,
                "realised_pnl_usd": pnl,
                "paper": True,
                "remaining_quantity": max(0.0, p.remaining_quantity),
            },
        )
        self.journal.accumulate_trade_pnl(
            p.trade_id or p.asset_key,
            pnl,
            final=final,
            asset_key=p.asset_key,
        )
        return pnl


def load_snapshots(path: str | Path) -> list[MarketSnapshot]:
    p = Path(path)
    rows: list[dict[str, Any]] = []
    if p.is_dir():
        for child in sorted(p.glob("*.json")):
            rows.append(json.loads(child.read_text(encoding="utf-8")))
    elif p.suffix.lower() == ".jsonl":
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    else:
        body = json.loads(p.read_text(encoding="utf-8"))
        rows.extend(body if isinstance(body, list) else [body])
    return sorted((MarketSnapshot.from_dict(r) for r in rows), key=lambda s: s.ts)
