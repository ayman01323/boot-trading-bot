from types import SimpleNamespace

import requests

from learnerbot import solana_jupiter_order_recovery_patch as patch


class _Executor:
    telegram_id = "123"
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()

    def _headers(self, json_body=False):
        return {}


class _Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._payload


def _valid_order():
    return {
        "transaction": "AA==",
        "requestId": "req-2",
        "router": "metis",
        "outAmount": "490000",
        "slippageBps": 50,
        "priceImpact": "0",
        "signatureFeeLamports": 5000,
        "prioritizationFeeLamports": 1000,
        "rentFeeLamports": 0,
        "feeBps": 0,
        "routePlan": [{"swapInfo": {"label": "one-hop"}}],
    }


def test_orphan_broadcast_fee_parameter_is_not_sent_when_no_manual_fee(monkeypatch):
    calls = []
    monkeypatch.setattr(patch._eff, "_cfg", lambda app: {
        "live_order_slippage_bps": "50",
        "live_max_total_fee_lamports": "5000",
        "live_max_fee_ratio_pct": "0",
        "live_expected_profit_margin_pct": "0",
        "live_max_fee_share_of_expected_profit_pct": "0",
        "live_enable_jito_tip": "false",
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
        "live_max_rent_exposure_lamports": "3000000",
    })
    monkeypatch.setattr(patch._eff, "dynamic_fee_cap_lamports", lambda cfg, trade: 5000)
    monkeypatch.setattr(patch._eff, "_validate_order", lambda *args, **kwargs: dict(args[1]))

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response(200, _valid_order())

    monkeypatch.setattr(patch.requests, "get", fake_get)
    patch.order_with_http400_recovery(_Executor(), patch._sol.WSOL_MINT, "mint", 500000)
    assert calls
    assert "priorityFeeLamports" not in calls[-1]
    assert "jitoTipLamports" not in calls[-1]
    assert "broadcastFeeType" not in calls[-1]


def test_http_400_manual_fee_order_retries_once_without_fee_controls(monkeypatch):
    calls = []
    monkeypatch.setattr(patch._eff, "_cfg", lambda app: {
        "live_order_slippage_bps": "50",
        "live_enable_jito_tip": "false",
    })
    monkeypatch.setattr(patch._eff, "dynamic_fee_cap_lamports", lambda cfg, trade: 12500)
    monkeypatch.setattr(patch._eff, "_validate_order", lambda *args, **kwargs: dict(args[1]))

    def fake_get(url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        calls.append(params)
        if len(calls) == 1:
            return _Response(400, {"requestId": "bad-1", "error": "invalid manual fee request"})
        return _Response(200, _valid_order())

    monkeypatch.setattr(patch.requests, "get", fake_get)
    result = patch.order_with_http400_recovery(_Executor(), patch._sol.WSOL_MINT, "mint", 500000)
    assert len(calls) == 2
    assert calls[0]["priorityFeeLamports"] == "7500"
    assert calls[0]["broadcastFeeType"] == "maxCap"
    assert "priorityFeeLamports" not in calls[1]
    assert "broadcastFeeType" not in calls[1]
    assert result["_jupiter_http400_safe_retry"] is True


def test_persistent_400_exposes_jupiter_error_body(monkeypatch):
    monkeypatch.setattr(patch._eff, "_cfg", lambda app: {
        "live_order_slippage_bps": "50",
        "live_enable_jito_tip": "false",
    })
    monkeypatch.setattr(patch._eff, "dynamic_fee_cap_lamports", lambda cfg, trade: 5000)
    monkeypatch.setattr(
        patch.requests,
        "get",
        lambda *args, **kwargs: _Response(400, {"requestId": "req-x", "error": "No route found"}),
    )
    try:
        patch.order_with_http400_recovery(_Executor(), patch._sol.WSOL_MINT, "mint", 500000)
    except patch._exec.SolanaLiveError as exc:
        text = str(exc)
        assert "HTTP 400" in text
        assert "No route found" in text
        assert "api.jup.ag" not in text
    else:
        raise AssertionError("expected SolanaLiveError")
