from __future__ import annotations

from types import SimpleNamespace

import pytest

from learnerbot import solana_execution_efficiency_patch as eff
from learnerbot import solana_liquidity_fail_closed_patch as liquidity


class _Executor:
    telegram_id = "123"
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()


def _cfg():
    return {
        "live_require_price_impact_quote": "true",
        "live_order_slippage_bps": "50",
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
        "live_max_rent_exposure_lamports": "3000000",
    }


def _order(**overrides):
    data = {
        "transaction": "AA==",
        "requestId": "req",
        "router": "metis",
        "outAmount": "500000",
        "slippageBps": 50,
        "signatureFeeLamports": 5000,
        "prioritizationFeeLamports": 0,
        "rentFeeLamports": 0,
        "feeBps": 0,
        "routePlan": [{"swapInfo": {"label": "one"}}],
    }
    data.update(overrides)
    return data


def test_missing_price_impact_is_rejected(monkeypatch):
    monkeypatch.setattr(eff, "_guard_event", lambda *args, **kwargs: None)
    with pytest.raises(eff._exec.SolanaLiveError, match="price impact is unavailable"):
        liquidity.validate_order_fail_closed_on_unknown_liquidity(
            _Executor(), _order(), eff._sol.WSOL_MINT, "mint", 500_000,
            500_000, 12_500, _cfg(),
        )


def test_zero_price_impact_is_valid_when_explicitly_reported(monkeypatch):
    monkeypatch.setattr(eff, "_guard_event", lambda *args, **kwargs: None)
    result = liquidity.validate_order_fail_closed_on_unknown_liquidity(
        _Executor(), _order(priceImpact="0"), eff._sol.WSOL_MINT, "mint", 500_000,
        500_000, 12_500, _cfg(),
    )
    assert result["_price_impact_bps"] == "0"
