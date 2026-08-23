from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_pool_risk_gate as pool

HOOD = "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV"


def _cfg(**overrides):
    cfg = {
        "max_roundtrip_loss_pct": "3",
        "live_pool_reference_probe_sol": "0.01",
        "live_pool_reference_max_impact_bps": "200",
        "live_pool_rugcheck_hard_score": "70",
        "live_pool_min_lp_locked_pct": "50",
        "live_pool_new_pair_cooling_seconds": "900",
        "live_pool_soft_risk_cooling_seconds": "3600",
        "live_pool_volume_liquidity_soft_ratio": "50",
        "live_pool_cross_price_soft_ratio": "5",
        "live_pool_material_pair_min_usd": "100",
    }
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


def _event(mint=HOOD):
    return {
        "action": "BUY",
        "mint": mint,
        "sol_amount": "0.001",
        "token_amount_raw": "1000000",
        "event_ts": int(time.time()),
        "leader_wallet": "Leader11111111111111111111111111111111111",
    }


def _pair(*, created, liquidity=10000, volume=1000, price="0.000001", dex="pumpfun"):
    return {
        "chainId": "solana",
        "dexId": dex,
        "pairCreatedAt": int(created * 1000),
        "priceNative": str(price),
        "liquidity": {"usd": float(liquidity), "quote": 10.0},
        "volume": {"h24": float(volume)},
        "quoteToken": {"address": pool._sol.WSOL_MINT},
    }


def test_hood_shape_reference_depth_hard_blocks_100_percent_impact(monkeypatch):
    def quote(app, input_mint, output_mint, amount_raw):
        assert input_mint == HOOD
        assert output_mint == pool._sol.WSOL_MINT
        return {"outAmount": "1", "priceImpact": 100.0}

    monkeypatch.setattr(pool._sol, "jupiter_quote", quote)
    result = pool.reference_reverse_depth_check(SimpleNamespace(), _event(), _cfg())
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "THIN_REFERENCE_DEPTH"
    assert result["evidence"]["reference_reverse_price_impact_bps"] == Decimal("10000.0")
    # This bot trades only 0.0005-0.005 SOL, so the anti-rug reference probe is
    # deliberately 0.01 SOL, not the 0.1-0.5 SOL generic suggestion.
    assert result["evidence"]["reference_probe_sol"] == Decimal("0.01")


def test_reference_probe_passes_deep_executable_market(monkeypatch):
    monkeypatch.setattr(
        pool._sol,
        "jupiter_quote",
        lambda *args, **kwargs: {"outAmount": "9900000", "priceImpact": 0.5},
    )
    result = pool.reference_reverse_depth_check(SimpleNamespace(), _event(), _cfg())
    assert result["decision"] == "PASS"
    assert result["reason_code"] == "REFERENCE_DEPTH_PASS"
    assert result["evidence"]["reference_reverse_price_impact_bps"] == Decimal("50.0")


def test_rugcheck_severe_authority_or_score_hard_blocks():
    result = pool.evaluate_rugcheck(
        {
            "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "score_normalised": 10,
            "lpLockedPct": 100,
            "risks": [{"name": "Freeze Authority still enabled", "level": "warn"}],
        },
        _cfg(),
    )
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "TOKEN_SECURITY_SEVERE"

    result = pool.evaluate_rugcheck(
        {"score_normalised": 80, "lpLockedPct": 100, "risks": []}, _cfg()
    )
    assert result["decision"] == "HARD_BLOCK"


def test_lp_concentration_is_shadow_only_not_fraud_claim():
    result = pool.evaluate_rugcheck(
        {
            "score_normalised": 20,
            "lpLockedPct": 25,
            "risks": [{"name": "Low amount of LP Providers", "level": "warn"}],
        },
        _cfg(),
    )
    assert result["decision"] == "SHADOW_ONLY"
    assert result["reason_code"] == "LP_CONCENTRATION_RISK"
    assert "fraud" not in result["reason"].lower()


def test_legitimate_migration_is_not_blocked_after_cooling():
    now = 1_800_000_000.0
    pairs = [
        _pair(created=now - 7200, liquidity=50000, volume=50000, price="0.000001", dex="pumpswap"),
        _pair(created=now - 1800, liquidity=48000, volume=30000, price="0.00000102", dex="meteora"),
    ]
    pool._LIQ_HISTORY.clear()
    result = pool.evaluate_dexscreener(pairs, _cfg(), mint="legit", now_epoch=now)
    assert result["decision"] == "PASS"
    assert result["evidence"]["dex_cross_pool_price_ratio"] < Decimal("1.1")


def test_new_material_pool_gets_cooling_not_hard_block():
    now = 1_800_000_000.0
    pairs = [_pair(created=now - 60, liquidity=20000, volume=5000)]
    pool._LIQ_HISTORY.clear()
    result = pool.evaluate_dexscreener(pairs, _cfg(), mint="fresh", now_epoch=now)
    assert result["decision"] == "COOLING"
    assert result["reason_code"] == "POOL_NEW_COOLING"


def test_extreme_fresh_cross_pool_discontinuity_cools():
    now = 1_800_000_000.0
    pairs = [
        _pair(created=now - 7200, liquidity=1000, volume=5000, price="0.000000154", dex="pumpswap"),
        _pair(created=now - 1200, liquidity=900, volume=5000, price="0.0000109", dex="meteora"),
    ]
    pool._LIQ_HISTORY.clear()
    result = pool.evaluate_dexscreener(pairs, _cfg(), mint=HOOD, now_epoch=now)
    assert result["decision"] == "COOLING"
    assert result["reason_code"] == "CROSS_POOL_PRICE_DISCONTINUITY"
    assert result["evidence"]["dex_cross_pool_price_ratio"] > Decimal("70")


def test_tiny_spam_pool_does_not_contaminate_deep_market_price_divergence():
    now = 1_800_000_000.0
    pairs = [
        _pair(created=now - 7200, liquidity=100000, volume=500000, price="0.000001", dex="orca"),
        _pair(created=now - 60, liquidity=100, volume=10, price="0.1", dex="spam"),
    ]
    pool._LIQ_HISTORY.clear()
    result = pool.evaluate_dexscreener(pairs, _cfg(), mint="deep", now_epoch=now)
    assert result["decision"] == "PASS"
    assert result["evidence"]["dex_material_pair_count"] == 1


def test_observed_liquidity_drop_below_30_percent_hard_blocks():
    now = 1_800_000_000.0
    pool._LIQ_HISTORY.clear()
    old = [_pair(created=now - 7200, liquidity=100000, volume=1000)]
    new = [_pair(created=now - 7200, liquidity=20000, volume=1000)]
    first = pool.evaluate_dexscreener(old, _cfg(), mint="drop", now_epoch=now - 60)
    assert first["decision"] == "PASS"
    second = pool.evaluate_dexscreener(new, _cfg(), mint="drop", now_epoch=now)
    assert second["decision"] == "HARD_BLOCK"
    assert second["reason_code"] == "POOL_LIQUIDITY_COLLAPSE"
    assert second["evidence"]["dex_liquidity_retained_pct"] == Decimal("20")
