from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sibot1_engines._shared.contracts import MarketEvent
from sibot1_engines._shared.live_atomic_cycle_export import _route_contract
from sibot1_engines._shared.market_relaxation_patch import _recent_mints_relaxed
from sibot1_engines.gpt.engine import _score_atomic_cycle


def _event(**overrides):
    payload = {
        "quote_age_ms": 500,
        "exact_quote_ok": True,
        "simulation_ok": False,
        "liquidity_ok": True,
        "sellability_ok": False,
        "route_approved": True,
        "whole_route_approved": True,
        "atomic_profit_protection": False,
        "route_path": ("0xA", "0xB", "0xC", "0xA"),
        "gross_edge_bps": "30",
        "estimated_cost_bps": "10",
        "source_path": "/tmp/direct_market_opportunities.csv",
        "venue_plan": (),
    }
    payload.update(overrides.pop("payload", {}))
    return MarketEvent(
        event_id="evt-1",
        chain="base",
        observed_at_ms=int(time.time() * 1000),
        source="test",
        event_type="evm_route",
        asset_in="0xA",
        asset_out="0xB",
        source_age_ms=0,
        payload=payload,
        **overrides,
    )


def test_gpt_nomination_does_not_require_wallet_specific_preflight_at_source():
    settings = SimpleNamespace(max_quote_age_ms=15_000, min_net_edge_bps=Decimal("12"))
    result = _score_atomic_cycle(_event(), settings)
    assert result is not None
    assert result["net_edge_bps"] == Decimal("20")
    assert result["source_preflight_complete"] is False
    assert result["live_revalidation_required"] is True


def test_gpt_nomination_still_requires_current_quote_and_route_evidence():
    settings = SimpleNamespace(max_quote_age_ms=15_000, min_net_edge_bps=Decimal("12"))
    event = _event(payload={"exact_quote_ok": False})
    assert _score_atomic_cycle(event, settings) is None


def test_atomic_export_infers_only_canonical_exact_v2_cycle():
    row = {
        "route_path": "0xA>0xB>0xC>0xA",
        "router_address": "0xRouter",
        "scanner_exact": "true",
        "source_verified": "true",
    }
    assert _route_contract(row, "/x/direct_market_opportunities.csv") == (
        "V2_CYCLE",
        "LIVE_REVALIDATE_REQUIRED",
    )
    assert _route_contract({**row, "scanner_exact": "false"}, "/x/direct_market_opportunities.csv") is None
    assert _route_contract({**row, "execution_mode": "SHADOW"}, "/x/direct_market_opportunities.csv") is None


def test_solana_relaxed_source_prefers_fresh_then_recent_then_profitable_history(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "solana_sibot.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE leader_events(
          event_id TEXT PRIMARY KEY, leader_wallet TEXT, signature TEXT, action TEXT,
          mint TEXT, decimals INTEGER, token_amount_raw TEXT, sol_amount TEXT,
          sell_pct REAL, slot INTEGER, event_ts INTEGER, created_at INTEGER
        );
        CREATE TABLE trades(
          trade_id TEXT PRIMARY KEY, wallet TEXT, mint TEXT, decimals INTEGER,
          buy_signature TEXT, sell_signature TEXT, buy_ts INTEGER, sell_ts INTEGER,
          token_amount_raw TEXT, cost_sol TEXT, proceeds_sol TEXT, net_sol TEXT,
          hold_seconds INTEGER, source TEXT, updated_at INTEGER
        );
        """
    )
    now = int(time.time())
    rows = [
        ("e1", "w", "sig-fresh", "BUY", "MINT_FRESH", 6, "1", "1", None, 1, now - 60, now),
        ("e2", "w", "sig-recent", "BUY", "MINT_RECENT", 6, "1", "1", None, 2, now - 3600, now),
    ]
    conn.executemany("INSERT INTO leader_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute(
        "INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("t1", "w", "MINT_HISTORY", 6, "buy-h", "sell-h", now - 7200, now - 7100, "1", "1", "2", "1", 100, "test", now),
    )
    conn.commit()
    conn.close()

    source = SimpleNamespace(data_dir=data, max_mints=3)
    result = _recent_mints_relaxed(source, now)
    assert [r[0] for r in result] == ["MINT_FRESH", "MINT_RECENT", "MINT_HISTORY"]
