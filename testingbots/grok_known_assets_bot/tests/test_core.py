import json
import time

import pytest

from grok_known_assets_bot.core import Journal, MarketSnapshot, StrategyEngine, load_config


def write_config(tmp_path, **risk_overrides):
    cfg = {"assets": [
        {"key": "solana:SOL:NATIVE", "chain": "solana", "symbol": "SOL", "address": "NATIVE", "enabled": True},
        {"key": "solana:MEME:X", "chain": "solana", "symbol": "MEME", "address": "VerifiedMint111", "enabled": True}],
        "risk": {"min_liquidity_usd": 100000, "min_volume_5m_usd": 10000, **risk_overrides}, "live": {"enabled": False}}
    p = tmp_path / "config.json"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(cfg)); return p


def snap(asset="solana:SOL:NATIVE", ts=1000.0, **kw):
    row = dict(asset_key=asset, ts=ts, bid=99.9, ask=100.0, reverse_bid=99.9, liquidity_usd=1_000_000,
               volume_5m_usd=100_000, ret_1m_pct=0.2, ret_5m_pct=1.0, ret_15m_pct=2.0, vol_5m_pct=2.0,
               spread_bps=10, price_impact_bps=10, fee_bps=5, sellable=True)
    row.update(kw); return MarketSnapshot.from_dict(row)


def engine(tmp_path, **risk):
    assets, rc, _ = load_config(write_config(tmp_path, **risk)); return StrategyEngine(assets, rc, Journal(tmp_path / "state.sqlite3"))


def test_unlisted_token_is_rejected(tmp_path):
    d = engine(tmp_path).evaluate_entry(snap(asset="solana:SCAM:unknown"), 10000, now=1000)
    assert d.action == "REJECT" and "allow-list" in d.reason


def test_stale_quote_rejected(tmp_path):
    d = engine(tmp_path, max_quote_age_s=5).evaluate_entry(snap(ts=900), 10000, now=1000)
    assert d.action == "REJECT" and "STALE_QUOTE" in d.reason


def test_position_sizing_respects_gross_cap(tmp_path):
    d = engine(tmp_path, risk_per_trade_pct=0.5, max_gross_position_pct=1.0).evaluate_entry(snap(), 10000, now=1000)
    assert d.action == "ENTER" and d.size_usd <= 100.000001


def test_daily_loss_breaker(tmp_path):
    now = time.time(); e = engine(tmp_path, daily_realised_loss_pct=2.0); e.start_of_day_equity = 10000
    e.journal.event("CLOSE", "solana:SOL:NATIVE", {"realised_pnl_usd": -250, "reason": "HARD_STOP"})
    d = e.evaluate_entry(snap(ts=now), 9750, now=now)
    assert d.action == "REJECT" and d.reason == "DAILY_LOSS_BREAKER"


def test_hard_stop_and_profit_take(tmp_path):
    e = engine(tmp_path / "one"); s = snap(); p = e.open_paper(s, e.evaluate_entry(s, 10000, now=1000))
    d = e.evaluate_exit(p, snap(ts=1010, bid=95.0, ask=95.1, reverse_bid=95.0, ret_1m_pct=-1.0), now=1010)
    assert d.action == "EXIT" and d.reason == "HARD_STOP"
    e2 = engine(tmp_path / "two"); s2 = snap(); p2 = e2.open_paper(s2, e2.evaluate_entry(s2, 10000, now=1000))
    d2 = e2.evaluate_exit(p2, snap(ts=1010, bid=103.0, ask=103.1, reverse_bid=103.0), now=1010)
    assert d2.action == "EXIT" and d2.reason == "TAKE_PROFIT_1" and d2.exit_fraction == 0.5


def test_enabled_placeholder_forbidden(tmp_path):
    p = tmp_path / "bad.json"; p.write_text(json.dumps({"assets": [{"key": "solana:BONK:X", "chain": "solana", "symbol": "BONK", "address": "MINT_REQUIRED_BONK", "enabled": True}]}))
    with pytest.raises(ValueError): load_config(p)
