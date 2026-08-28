from __future__ import annotations

from grok_known_assets_bot.core import MarketSnapshot
from grok_known_assets_bot.live_feed import LiveFeedSettings
import grok_known_assets_bot.live_readiness as live_readiness


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        asset_key="solana:SOL:NATIVE",
        ts=1000.0,
        bid=99.9,
        ask=100.0,
        reverse_bid=99.9,
        liquidity_usd=10_000_000.0,
        volume_5m_usd=500_000.0,
        ret_1m_pct=0.05,
        ret_5m_pct=0.10,
        ret_15m_pct=0.20,
        vol_5m_pct=0.1,
        spread_bps=10.0,
        price_impact_bps=20.0,
        fee_bps=5.0,
        sellable=True,
        slippage_bps=50.0,
    )


def test_live_readiness_passes_fresh_entry_reverse_and_stress(monkeypatch):
    calls: list[tuple[str, str, int]] = []

    def fake_quote(input_mint, output_mint, amount, *, slippage_bps, timeout):
        calls.append((input_mint, output_mint, amount))
        if len(calls) == 1:
            return {"outAmount": "9000000", "priceImpactPct": "0.001", "routePlan": []}
        if len(calls) == 2:
            return {"outAmount": "882000", "priceImpactPct": "0.0015", "routePlan": []}
        return {"outAmount": "2646000", "priceImpactPct": "0.003", "routePlan": []}

    monkeypatch.setattr(live_readiness, "_quote", fake_quote)
    result = live_readiness.assess_live_readiness(_snapshot(), LiveFeedSettings(), now=1000.0)
    assert result.ready is True
    assert result.reason == "LIVE_ROUTE_PREFLIGHT_PASS"
    assert result.entry_target_sol == 0.009
    assert result.estimated_spend_usdc == 0.9
    assert result.quoted_sol_out == 0.009
    assert result.reverse_recovery_usdc == 0.882
    assert result.roundtrip_loss_pct < 3.0
    assert result.signing_enabled is False
    assert result.broadcast_enabled is False
    assert len(calls) == 3


def test_live_readiness_rejects_excessive_reverse_impact(monkeypatch):
    calls = 0

    def fake_quote(_input_mint, _output_mint, _amount, *, slippage_bps, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"outAmount": "9000000", "priceImpactPct": "0.001", "routePlan": []}
        if calls == 2:
            return {"outAmount": "882000", "priceImpactPct": "0.03", "routePlan": []}
        return {"outAmount": "2646000", "priceImpactPct": "0.003", "routePlan": []}

    monkeypatch.setattr(live_readiness, "_quote", fake_quote)
    result = live_readiness.assess_live_readiness(_snapshot(), LiveFeedSettings(), now=1000.0)
    assert result.ready is False
    assert result.reason == "REVERSE_IMPACT_TOO_HIGH"
    assert result.signing_enabled is False
    assert result.broadcast_enabled is False


def test_live_readiness_rejects_stale_signal_without_network(monkeypatch):
    def should_not_quote(*_args, **_kwargs):
        raise AssertionError("stale signal must fail before quote")

    monkeypatch.setattr(live_readiness, "_quote", should_not_quote)
    result = live_readiness.assess_live_readiness(_snapshot(), LiveFeedSettings(), now=1021.0)
    assert result.ready is False
    assert result.reason == "SIGNAL_TOO_OLD"
