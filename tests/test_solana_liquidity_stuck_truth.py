from __future__ import annotations

from decimal import Decimal

from learnerbot import solana_trade_gate_truth_patch as truth


def test_liquidity_stuck_is_reporting_only_and_derived_from_backoff(monkeypatch) -> None:
    monkeypatch.setattr(
        truth._emergency,
        "_load_backoff",
        lambda app, position_id: {
            "attempts": 4,
            "next_retry": 2_000_000_000,
            "first_blocked_epoch": 1_900_000_000,
        },
    )
    monkeypatch.setattr(truth.time, "time", lambda: 1_999_999_900)

    state = truth._liquidity_state(object(), "position-1")

    assert state["label"] == "LIQUIDITY_STUCK"
    assert state["attempts"] == 4
    assert state["retry_after_seconds"] == 100


def test_no_backoff_keeps_plain_open_reporting(monkeypatch) -> None:
    monkeypatch.setattr(truth._emergency, "_load_backoff", lambda app, position_id: {})
    state = truth._liquidity_state(object(), "position-1")
    assert state["label"] == "OPEN"
    assert state["attempts"] == 0


def test_whynotrade_explains_stuck_position_still_counts_as_open(monkeypatch) -> None:
    monkeypatch.setattr(truth, "_PREV_BUILD_REPORT", lambda app, tid: "BASE")
    monkeypatch.setattr(
        truth,
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
            "open_live_positions": [{
                "position_id": "07d9f95e7dbb77288b2d4abca53e3949",
                "mint": "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV",
                "recorded_raw": "87400222",
                "verified": True,
                "verified_balance_raw": "87405554",
                "wallets_checked": 1,
                "liquidity_state": "LIQUIDITY_STUCK",
                "liquidity_attempts": 3,
                "liquidity_retry_after_seconds": 60,
                "safe_slice_percentages": ["100", "75", "50", "25", "10", "5", "2", "1"],
                "emergency_limit_bps": "500",
            }],
            "leaders": [],
        },
    )

    report = truth.build_report_with_gate_truth(object(), "123")

    assert "LIQUIDITY_STUCK" in report
    assert "8fipYA8kSkzHgcXUdKVgdh3CvoMhXR6kAo74693M3fPV" in report
    assert "100/75/50/25/10/5/2/1%" in report
    assert "hard ceiling <b>5.00%</b>" in report
    assert "Remains <b>OPEN</b> for recovery/risk/exposure accounting" in report
