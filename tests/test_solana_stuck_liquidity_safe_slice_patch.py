from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import solana_emergency_liquidity_unwind_patch as emergency
from learnerbot import solana_sibot as sol
from learnerbot import solana_stuck_liquidity_safe_slice_patch as patch
from learnerbot.solana_live_executor import SolanaLiveError


def test_extended_slice_ladder_reaches_one_percent_without_changing_ceiling() -> None:
    assert list(emergency._SLICE_FRACTIONS) == [
        Decimal("1"),
        Decimal("0.75"),
        Decimal("0.50"),
        Decimal("0.25"),
        Decimal("0.10"),
        Decimal("0.05"),
        Decimal("0.02"),
        Decimal("0.01"),
    ]
    assert emergency._emergency_limit({}) == Decimal("500")


def test_slice_ladder_continues_below_25_percent_and_can_accept_one_percent(monkeypatch) -> None:
    attempted = []

    def fake_close(app, tid, position, fraction, reason):
        attempted.append(Decimal(str(fraction)))
        if Decimal(str(fraction)) == Decimal("0.01"):
            return {"closed": False, "signature": "ok"}
        raise SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 50 bps = 10050.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(emergency, "_BASE_CLOSE", fake_close)
    result, failures = emergency._attempt_slices(
        object(),
        "123",
        {"position_id": "p1"},
        Decimal("1"),
        "SOLANA_STOP_LOSS",
        "SOLANA_STOP_LOSS",
    )

    assert attempted == list(emergency._SLICE_FRACTIONS)
    assert result is not None
    assert result["liquidity_adaptive_fraction"] == "0.01"
    assert failures is None


def test_near_100_percent_impact_is_never_bypassed_at_any_slice(monkeypatch) -> None:
    attempted = []

    def always_unsafe(app, tid, position, fraction, reason):
        attempted.append(Decimal(str(fraction)))
        raise SolanaLiveError(
            "Economic execution guard: quoted price impact 10000.00 bps + slippage 50 bps = 10050.00 bps exceeds 500 bps"
        )

    monkeypatch.setattr(emergency, "_BASE_CLOSE", always_unsafe)
    result, failures = emergency._attempt_slices(
        object(),
        "123",
        {"position_id": "p1"},
        Decimal("1"),
        "SOLANA_STOP_LOSS",
        "SOLANA_STOP_LOSS",
    )

    assert result is None
    assert attempted == list(emergency._SLICE_FRACTIONS)
    assert len(failures or []) == len(emergency._SLICE_FRACTIONS)
    assert emergency._emergency_limit({}) == Decimal("500")


def test_automatic_loss_slice_rejects_dust_net_output_before_broadcast(monkeypatch) -> None:
    monkeypatch.setattr(
        patch,
        "_PREV_VALIDATE",
        lambda *args, **kwargs: {
            "transaction": "unsigned",
            "_trade_value_lamports": 25_000,
            "_total_fee_equiv_lamports": 5_000,
        },
    )
    executor = SimpleNamespace(telegram_id="123", app=SimpleNamespace())
    token = emergency._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        with pytest.raises(SolanaLiveError, match="net proceeds after fees"):
            patch.validate_order_with_safe_slice_floor(
                executor,
                {},
                "TokenMint",
                sol.WSOL_MINT,
                100,
                25_000,
                100_000,
                {"estimated_exit_fee_sol": ".00002", "live_emergency_exit_min_net_lamports": "10000"},
            )
    finally:
        emergency._EXIT_REASON.reset(token)


def test_automatic_loss_slice_allows_positive_non_dust_net_output(monkeypatch) -> None:
    validated = {
        "transaction": "unsigned",
        "_trade_value_lamports": 40_000,
        "_total_fee_equiv_lamports": 5_000,
    }
    monkeypatch.setattr(patch, "_PREV_VALIDATE", lambda *args, **kwargs: dict(validated))
    executor = SimpleNamespace(telegram_id="123", app=SimpleNamespace())
    token = emergency._EXIT_REASON.set("SOLANA_STOP_LOSS")
    try:
        result = patch.validate_order_with_safe_slice_floor(
            executor,
            {},
            "TokenMint",
            sol.WSOL_MINT,
            100,
            40_000,
            100_000,
            {"estimated_exit_fee_sol": ".00002", "live_emergency_exit_min_net_lamports": "10000"},
        )
    finally:
        emergency._EXIT_REASON.reset(token)
    assert result["_trade_value_lamports"] == 40_000


def test_dust_rejection_is_treated_as_safe_slice_retry_reason() -> None:
    exc = SolanaLiveError(
        "Economic execution guard: net proceeds after fees 5000 lamports below emergency minimum 10000 lamports"
    )
    assert emergency._prebroadcast_liquidity_reject(exc) is True
