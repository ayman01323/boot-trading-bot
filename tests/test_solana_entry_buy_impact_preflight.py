from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_entry_exit_liquidity_preflight_patch as patch
from learnerbot import solana_preflight_cache_patch as cache


def _cfg(**overrides):
    cfg = {
        "max_signal_age_seconds": "30",
        "max_roundtrip_loss_pct": "3",
        "max_entry_deterioration_pct": "2",
        "live_entry_require_exit_liquidity_max_bps": "500",
        "live_emergency_exit_max_combined_bps": "500",
        "live_order_slippage_bps": "50",
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
    }
    cfg.update({k: str(v) for k, v in overrides.items()})
    return cfg


def _event():
    return {
        "event_ts": int(time.time()),
        "signature": "sig-buy-impact",
        "mint": "Mint111111111111111111111111111111111111111",
        "leader_wallet": "Leader11111111111111111111111111111111111",
        "sol_amount": "1",
        "token_amount_raw": "1000000",
    }


def test_exact_reported_2587_bps_buy_impact_is_rejected_before_reverse_quote(monkeypatch):
    calls = []

    def fake_quote(app, input_mint, output_mint, amount_raw):
        calls.append((input_mint, output_mint, int(amount_raw)))
        return {
            "outAmount": "1000000",
            "priceImpact": "25.8767",  # percentage points -> 2587.67 bps
            "routePlan": [{"swapInfo": {"label": "Raydium"}}],
        }

    monkeypatch.setattr(patch._sol, "jupiter_quote", fake_quote)
    ok, reason, detail = patch.validate_entry_with_exit_liquidity(
        SimpleNamespace(), _event(), Decimal("0.0005"), _cfg()
    )

    assert ok is False
    assert "entry liquidity rejected" in reason
    assert "2587.67 bps" in reason
    assert "2637.67 bps exceeds 150 bps" in reason
    assert detail["entry_price_impact_bps"] == Decimal("2587.6700")
    assert detail["entry_combined_bps"] == Decimal("2637.6700")
    # Critical regression: do not waste a reverse quote or claim/execute after
    # the first BUY quote has already proved the intended entry is uneconomic.
    assert len(calls) == 1


def test_multihop_entry_uses_stricter_execution_ceiling(monkeypatch):
    calls = []

    def fake_quote(app, input_mint, output_mint, amount_raw):
        calls.append((input_mint, output_mint, int(amount_raw)))
        return {
            "outAmount": "1000000",
            "priceImpact": "0.60",  # 60 bps + 50 bps = 110 bps
            "routePlan": [
                {"swapInfo": {"label": "VenueA"}},
                {"swapInfo": {"label": "VenueB"}},
            ],
        }

    monkeypatch.setattr(patch._sol, "jupiter_quote", fake_quote)
    ok, reason, detail = patch.validate_entry_with_exit_liquidity(
        SimpleNamespace(), _event(), Decimal("0.0005"), _cfg()
    )

    assert ok is False
    assert "110.00 bps exceeds 100 bps" in reason
    assert detail["entry_route_hops"] == 2
    assert detail["entry_liquidity_limit_bps"] == Decimal("100")
    assert len(calls) == 1


def test_preflight_cache_key_changes_with_entry_execution_caps():
    event = _event()
    base = cache._key(event, Decimal("0.0005"), _cfg())
    combined = cache._key(
        event, Decimal("0.0005"), _cfg(live_max_combined_impact_slippage_bps=125)
    )
    multihop = cache._key(
        event, Decimal("0.0005"), _cfg(live_multihop_max_combined_bps=80)
    )
    assert base != combined
    assert base != multihop
