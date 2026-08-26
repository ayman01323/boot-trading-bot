from __future__ import annotations

from decimal import Decimal

from sibot1_engines._shared import deep_nomination_relaxation_patch as patch


def test_gemini_nomination_caps_strict_source_settings(tmp_path):
    p = tmp_path / "gemini.csv"
    p.write_text(
        "engine_id,engine_version,strategy_id,chain,trade_amount,min_liquidity_usd,min_volume_usd,min_volume_liquidity_ratio,max_volume_liquidity_ratio,min_liquidity_velocity_pct,min_lp_locked_pct_prefilter,signal_cooldown_ms,take_profit_pct,max_source_age_ms\n"
        "gemini,1.1.0,Gemini-PulseFlow,solana,1.5,5000,200,0.02,10,-35,25,300000,0.05,750\n",
        encoding="utf-8",
    )
    s = patch._gemini_load(p)
    assert s.min_liquidity_usd == Decimal("3000")
    assert s.min_volume_usd == Decimal("100")
    assert s.min_volume_liquidity_ratio == Decimal("0.01")
    assert s.max_volume_liquidity_ratio == Decimal("10")
    assert s.max_source_age_ms == 750


def test_gemini_does_not_raise_already_more_flexible_values(tmp_path):
    p = tmp_path / "gemini.csv"
    p.write_text(
        "engine_id,engine_version,strategy_id,chain,trade_amount,min_liquidity_usd,min_volume_usd,min_volume_liquidity_ratio,max_volume_liquidity_ratio,min_liquidity_velocity_pct,min_lp_locked_pct_prefilter,signal_cooldown_ms,take_profit_pct,max_source_age_ms\n"
        "gemini,1.1.0,Gemini-PulseFlow,solana,1.5,2000,80,0.005,10,-35,25,300000,0.05,750\n",
        encoding="utf-8",
    )
    s = patch._gemini_load(p)
    assert s.min_liquidity_usd == Decimal("2000")
    assert s.min_volume_usd == Decimal("80")
    assert s.min_volume_liquidity_ratio == Decimal("0.005")


def test_grok_relaxes_nomination_but_keeps_dev_unknown_fail_closed(tmp_path):
    p = tmp_path / "grok.csv"
    p.write_text(
        "chain,strategy_id,trade_amount,min_confidence,min_volume_velocity,take_profit_pct,stop_loss_pct,reject_dev_selling,max_source_age_ms\n"
        "solana,CompactFlow-v1,1,0.55,0.02,0.035,-0.018,true,750\n",
        encoding="utf-8",
    )
    s = patch._grok_load(p)
    assert s.min_confidence == Decimal("0.52")
    assert s.min_volume_velocity == Decimal("0.005")
    assert s.reject_dev_selling is True
    assert s.max_source_age_ms == 750
