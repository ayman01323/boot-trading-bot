from __future__ import annotations

from decimal import Decimal

from learnerbot import solana_trade_gate_truth_patch as patch


def test_whynotrade_shows_full_open_live_position_and_verified_balance(monkeypatch):
    full_mint = "7YwK9nCjYqJ1fXap7f81sCkA1n6t3Q2m5X9vP4AbCdEf"
    monkeypatch.setattr(patch, "_PREV_BUILD_REPORT", lambda app, tid: "BASE")
    monkeypatch.setattr(
        patch,
        "gate_snapshot",
        lambda app, tid: {
            "wallet": {
                "signing_ready": True,
                "balance_sol": Decimal("0.054512309"),
                "minimum_sol": Decimal("0.0105"),
            },
            "platform_ok": False,
            "platform_reason": "platform amount gate is in recovery mode and another LIVE position is still open",
            "platform_metrics": {"profit_factor": "0E+29"},
            "recovery_canary": False,
            "open_live_positions": [
                {
                    "position_id": "sol-pos-123",
                    "mint": full_mint,
                    "recorded_raw": "123456",
                    "verified": True,
                    "verified_balance_raw": "123450",
                    "wallets_checked": 2,
                }
            ],
            "leaders": [],
        },
    )

    report = patch.build_report_with_gate_truth(object(), "123")
    assert "sol-pos-123" in report
    assert full_mint in report
    assert "Recorded raw <b>123456</b>" in report
    assert "verified wallet raw <b>123450</b> across 2 wallet(s)" in report
    assert "NO SELECTED LEADER" in report


def test_whynotrade_marks_open_live_balance_unknown_when_proof_incomplete(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_BUILD_REPORT", lambda app, tid: "BASE")
    monkeypatch.setattr(
        patch,
        "gate_snapshot",
        lambda app, tid: {
            "wallet": {"signing_ready": True, "balance_sol": Decimal("1"), "minimum_sol": Decimal("0.01")},
            "platform_ok": False,
            "platform_reason": "cannot prove recovery canary exclusivity after LIVE-position reconciliation",
            "platform_metrics": {},
            "recovery_canary": False,
            "open_live_positions": [
                {
                    "position_id": "sol-pos-unknown",
                    "mint": "MintUnknown111111111111111111111111111111111",
                    "recorded_raw": "9",
                    "verified": False,
                    "verified_balance_raw": "",
                    "wallets_checked": 0,
                }
            ],
            "leaders": [],
        },
    )
    report = patch.build_report_with_gate_truth(object(), "123")
    assert "verified wallet raw <b>UNKNOWN</b>" in report
