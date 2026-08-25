from __future__ import annotations

"""Resilient, bounded candidate-source fallbacks for SiBot 1.

This patch changes discovery only. It never signs, broadcasts, changes ARMED/LIVE/AUTO,
or weakens PoolCheck / quote / simulation / execution safety.  The goal is to stop
healthy engines sitting at events=0 merely because a very narrow source window is quiet.
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import market_data as _market

_INSTALLED = False
_ORIG_SOURCE_INIT = _market.SharedBootMarketSource.__init__
_ORIG_SOURCE_POLL = _market.SharedBootMarketSource.poll

# Prefer genuinely fresh leader activity.  When it is quiet, keep a small current-
# market watchlist seeded by older *observed* leader buys and profitable reconstructed
# trades.  DexScreener is still fetched now, and all downstream strategy/PoolCheck/
# LIVE revalidation remains mandatory.
_PRIMARY_LEADER_SECONDS = 15 * 60
_FALLBACK_LEADER_SECONDS = 6 * 60 * 60
_HISTORICAL_SEED_SECONDS = 24 * 60 * 60
_HEALTH_INTERVAL_SECONDS = 30


def _recent_mints_relaxed(self, now: int) -> list[tuple[str, str, int]]:
    db = Path(self.data_dir) / "solana_sibot.sqlite3"
    if not db.exists():
        return []

    out: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    def add(rows) -> None:
        for mint, signature, event_ts in rows:
            mint = str(mint or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            out.append((mint, str(signature or ""), int(event_ts or now)))
            if len(out) >= self.max_mints:
                return

    try:
        conn = sqlite3.connect(db, timeout=2)
        # Tier 1: the original 15-minute leader window.
        rows = conn.execute(
            """SELECT mint,signature,event_ts FROM leader_events
               WHERE action='BUY' AND event_ts>=? AND mint IS NOT NULL AND mint!=''
               ORDER BY event_ts DESC LIMIT ?""",
            (now - _PRIMARY_LEADER_SECONDS, self.max_mints * 4),
        ).fetchall()
        add(rows)

        # Tier 2: if the selected leaders are simply quiet, retain their recently
        # observed mints as a watchlist and refresh *current* DEX evidence.
        if len(out) < self.max_mints:
            rows = conn.execute(
                """SELECT mint,signature,event_ts FROM leader_events
                   WHERE action='BUY' AND event_ts>=? AND mint IS NOT NULL AND mint!=''
                   ORDER BY event_ts DESC LIMIT ?""",
                (now - _FALLBACK_LEADER_SECONDS, self.max_mints * 8),
            ).fetchall()
            add(rows)

        # Tier 3: only if leader activity still cannot fill the bounded watchlist,
        # seed it with tokens from recent *profitable reconstructed* Solana trades.
        # This is discovery evidence only; it is never treated as a current edge.
        if len(out) < self.max_mints:
            rows = conn.execute(
                """SELECT mint,buy_signature,buy_ts FROM trades
                   WHERE sell_ts>=? AND CAST(net_sol AS REAL)>0
                     AND mint IS NOT NULL AND mint!=''
                   ORDER BY sell_ts DESC LIMIT ?""",
                (now - _HISTORICAL_SEED_SECONDS, self.max_mints * 8),
            ).fetchall()
            add(rows)
        conn.close()
    except Exception:
        return []
    return out[: self.max_mints]


def _source_init(self, csv_dir, data_dir, evidence) -> None:
    _ORIG_SOURCE_INIT(self, csv_dir, data_dir, evidence)
    self._relaxed_csv_dir = Path(csv_dir)
    self._relaxed_data_dir = Path(data_dir)
    self._relaxed_health_last = 0.0


def _safe_rows(path: Path) -> list[dict[str, str]]:
    try:
        return _market._rows(path)
    except Exception:
        return []


def _source_health(self, events: list[Any]) -> dict[str, Any]:
    now = int(time.time())
    evm = []
    for path in self.evm.paths:
        rows = _safe_rows(path)
        observed = []
        valid_fresh = 0
        for row in rows[-500:]:
            ts = _market._i(row.get("observed_at_epoch"), 0)
            if ts > 0:
                observed.append(ts)
                route = [x.strip() for x in str(row.get("route_path") or "").split(">") if x.strip()]
                if now - ts <= self.evm.max_age_seconds and len(route) >= 2:
                    valid_fresh += 1
        newest = max(observed) if observed else 0
        evm.append({
            "file": path.name,
            "exists": path.exists(),
            "rows": len(rows),
            "newest_age_seconds": (now - newest) if newest else None,
            "fresh_valid_rows": valid_fresh,
        })

    sol = {
        "db_exists": False,
        "leader_buys_15m": 0,
        "leader_buys_6h": 0,
        "profitable_trade_seeds_24h": 0,
        "newest_leader_buy_age_seconds": None,
    }
    db = self._relaxed_data_dir / "solana_sibot.sqlite3"
    if db.exists():
        sol["db_exists"] = True
        try:
            conn = sqlite3.connect(db, timeout=2)
            row = conn.execute(
                "SELECT COUNT(*),MAX(event_ts) FROM leader_events WHERE action='BUY' AND event_ts>=?",
                (now - _PRIMARY_LEADER_SECONDS,),
            ).fetchone()
            sol["leader_buys_15m"] = int(row[0] or 0)
            newest = int(row[1] or 0)
            row = conn.execute(
                "SELECT COUNT(*) FROM leader_events WHERE action='BUY' AND event_ts>=?",
                (now - _FALLBACK_LEADER_SECONDS,),
            ).fetchone()
            sol["leader_buys_6h"] = int(row[0] or 0)
            if not newest:
                row = conn.execute("SELECT MAX(event_ts) FROM leader_events WHERE action='BUY'").fetchone()
                newest = int((row or [0])[0] or 0)
            if newest:
                sol["newest_leader_buy_age_seconds"] = max(0, now - newest)
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE sell_ts>=? AND CAST(net_sol AS REAL)>0",
                (now - _HISTORICAL_SEED_SECONDS,),
            ).fetchone()
            sol["profitable_trade_seeds_24h"] = int(row[0] or 0)
            conn.close()
        except Exception as exc:
            sol["error"] = type(exc).__name__

    return {
        "schema_version": 1,
        "updated_epoch": now,
        "relaxed_discovery_only": True,
        "execution_safety_unchanged": True,
        "events_emitted": len(events),
        "events_by_chain": {
            "base": sum(1 for e in events if str(getattr(e, "chain", "")).lower() == "base"),
            "solana": sum(1 for e in events if str(getattr(e, "chain", "")).lower() == "solana"),
        },
        "evm": evm,
        "solana": sol,
        "windows_seconds": {
            "primary_leader": _PRIMARY_LEADER_SECONDS,
            "fallback_leader": _FALLBACK_LEADER_SECONDS,
            "profitable_trade_seed": _HISTORICAL_SEED_SECONDS,
        },
    }


def _write_health(self, events: list[Any]) -> None:
    now_mono = time.monotonic()
    if now_mono - float(getattr(self, "_relaxed_health_last", 0.0)) < _HEALTH_INTERVAL_SECONDS:
        return
    self._relaxed_health_last = now_mono
    try:
        payload = _source_health(self, events)
        out = self._relaxed_data_dir / "sibot1" / "market_source_health.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except Exception:
        pass


def _source_poll(self):
    events = _ORIG_SOURCE_POLL(self)
    _write_health(self, events)
    return events


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _market.SolanaLeaderDexSource._recent_mints = _recent_mints_relaxed
    _market.SharedBootMarketSource.__init__ = _source_init
    _market.SharedBootMarketSource.poll = _source_poll
    _INSTALLED = True


install()
