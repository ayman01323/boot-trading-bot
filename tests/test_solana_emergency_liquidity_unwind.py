from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import solana_emergency_liquidity_unwind_patch as patch


class _Executor:
    telegram_id = "123"
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()


def _cfg():
    return {
        "live_max_combined_impact_slippage_bps": "150",
        "live_multihop_max_combined_bps": "100",
        "live_emergency_exit_max_combined_bps": "500",
        "live_require_price_impact_quote": "true",
        "live_max_rent_exposure_lamports": "3000000",
    }


def _order(price_impact):
    return {
        "transaction": "AA==",
        "requestId": "req-1",
        "router": "metis",
        "outAmount": "1000000",
        "slippageBps": 30,
        "priceImpact": price_impact,
        "routePlan": [{"swapInfo": {"label": "one-hop"}}],
        "signatureFeeLamports": 5000,
        "prioritizationFeeLamports": 0,
        "rentFeeLamports": 0,
        "feeBps": 0,
    }


def test_normal_exit_keeps_strict_price_impact_guard():
    with pytest.raises(patch._exec.SolanaLiveError, match="quoted price impact"):
        patch.validate_order_with_emergency_liquidity(
            _Executor(),
            _order(4.5),  # 4.5% = 450 bps; 480 bps including slippage
            "mint",
            patch._sol.WSOL_MINT,
            1_000_000,
            1_000_000,
            100_000,
            _cfg(),
        )


def test_stop_loss_may_use_bounded_five_percent_exit_ceiling():
    token = patch._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        result = patch.validate_order_with_emergency_liquidity(
            _Executor(),
            _order(4.5),  # 480 bps combined: above ordinary 150, below emergency 500
            "mint",
            patch._sol.WSOL_MINT,
            1_000_000,
            1_000_000,
            100_000,
            _cfg(),
        )
    finally:
        patch._EXIT_REASON.reset(token)
    assert result["_price_impact_bps"] == "450.0"


def test_stop_loss_still_refuses_a_100_percent_impact_quote():
    token = patch._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        with pytest.raises(patch._exec.SolanaLiveError, match="10000.00 bps"):
            patch.validate_order_with_emergency_liquidity(
                _Executor(),
                _order(100),
                "mint",
                patch._sol.WSOL_MINT,
                1_000_000,
                1_000_000,
                100_000,
                _cfg(),
            )
    finally:
        patch._EXIT_REASON.reset(token)


def test_emergency_exit_retries_smaller_slice_only_after_prebroadcast_impact_reject(monkeypatch):
    patch._BACKOFF.clear()
    calls = []

    def fake_close(app, tid, position, fraction, reason):
        f = Decimal(str(fraction))
        calls.append((f, reason))
        if f in {Decimal("1"), Decimal("0.75")}:
            raise patch._exec.SolanaLiveError(
                "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
            )
        return {"closed": False, "signature": "sig", "reason": reason}

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app: _cfg())

    result = patch.close_live_with_emergency_liquidity_unwind(
        SimpleNamespace(),
        "123",
        {"position_id": "p1"},
        Decimal(1),
        "SOLANA_STOP_LOSS",
    )

    assert [row[0] for row in calls] == [Decimal("1"), Decimal("0.75"), Decimal("0.50")]
    assert calls[-1][1] == "SOLANA_STOP_LOSS_LIQUIDITY_PARTIAL_50PCT"
    assert result["liquidity_adaptive_fraction"] == "0.50"
    assert patch._backoff_remaining("p1") > 0


def test_all_unsafe_slices_defer_without_broadcast_retry_spam(monkeypatch):
    patch._BACKOFF.clear()
    calls = []
    notices = []

    def fake_close(app, tid, position, fraction, reason):
        calls.append(Decimal(str(fraction)))
        raise patch._exec.SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 30 bps = 10030.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(patch, "_BASE_CLOSE", fake_close)
    monkeypatch.setattr(patch._sol, "settings", lambda app: _cfg())
    monkeypatch.setattr(patch._live, "_notify", lambda app, tid, text: notices.append(text))

    first = patch.close_live_with_emergency_liquidity_unwind(
        SimpleNamespace(),
        "123",
        {"position_id": "p2"},
        Decimal(1),
        "SOLANA_LEADER_EXIT_LOSS_CAP",
    )
    first_call_count = len(calls)

    second = patch.close_live_with_emergency_liquidity_unwind(
        SimpleNamespace(),
        "123",
        {"position_id": "p2"},
        Decimal(1),
        "SOLANA_LEADER_EXIT_LOSS_CAP",
    )

    assert calls == [Decimal("1"), Decimal("0.75"), Decimal("0.50"), Decimal("0.25")]
    assert first_call_count == 4
    assert first["deferred"] is True
    assert second["deferred"] is True
    assert len(calls) == first_call_count
    assert len(notices) == 1
    assert "No transaction was broadcast" in notices[0]
    assert "100%, 75%, 50% and 25%" in notices[0]
