from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import sirisky.stage3_risk as riskmod
import sirisky.stage6_monitor as monmod


class FakeSettings:
    def __init__(self, root: Path):
        self.csv_dir = root

    def risk(self):
        return {
            "max_open_positions": "1",
            "min_exit_health_pct": "85",
            "min_forecast_net_pct": "0.25",
            "max_round_trip_cost_pct": "8",
            "untouched_sol_reserve": "0.005",
            "lp_recent_sell_sim_max_age_sec": "300",
            "fast_take_profit_floor_pct": "2",
            "fast_take_profit_cap_pct": "5",
            "fast_stop_net_pct": "3",
            "warm_reversal_pct": "1.5",
            "hot_reversal_pct": "3",
            "fast_max_hold_cap_seconds": "300",
        }


def opportunity(meta=None):
    snap = SimpleNamespace(
        exit_health_pct=99.0,
        round_trip_cost_pct=1.0,
        meta=dict(meta or {}),
    )
    return SimpleNamespace(
        temperature="COLD",
        forecast_net_pct=2.5,
        position_sol=0.0005,
        snapshot=snap,
    )


def test_stage3(root: Path):
    settings = FakeSettings(root)
    # Ensure the real CSV reader sees an empty, valid open-position table.
    (root / "open_positions.csv").write_text("status\n", encoding="utf-8")
    riskmod.WalletStore = lambda _settings: SimpleNamespace(address=lambda: "wallet")
    riskmod.wallet_balance_lamports = lambda _settings, _address: 100_000_000

    clean = riskmod.Stage3Risk(settings).check(opportunity({"reverse_quote_present": True}))
    assert clean.passed, clean.reasons

    blocked = riskmod.Stage3Risk(settings).check(
        opportunity({"lp_concentration_risk": True, "reverse_quote_present": True})
    )
    assert not blocked.passed
    assert any("LP_CONCENTRATION_RISK" in x for x in blocked.reasons)

    conditional = riskmod.Stage3Risk(settings).check(
        opportunity(
            {
                "lp_concentration_risk": True,
                "reverse_quote_present": True,
                "lp_depth_test_pass": True,
                "lp_depth_test_slippage_pct": 1.2,
                "recent_sell_sim_age_sec": 30,
                "lp_unlock_transparent": True,
                "no_recent_liquidity_withdrawal": True,
            }
        )
    )
    assert conditional.passed, conditional.reasons
    assert any(x.startswith("ADVISORY:LP_CONCENTRATION_RISK") for x in conditional.reasons)

    catastrophic = riskmod.Stage3Risk(settings).check(
        opportunity({"reverse_quote_present": True, "active_liquidity_removal": True})
    )
    assert not catastrophic.passed
    assert "ACTIVE_LIQUIDITY_REMOVAL" in catastrophic.reasons


def test_stage6(root: Path):
    settings = FakeSettings(root)
    monmod.WalletStore = lambda _settings: SimpleNamespace(address=lambda: "wallet")

    # 2% executable-net take-profit exits immediately.
    monmod.quote_only = lambda *_args, **_kwargs: {"out_amount": 1_020_000}
    monitor = monmod.Stage6Monitor(settings)
    pos = {
        "position_id": "p-tp",
        "mint": "mint",
        "token_raw": 100,
        "entry_lamports": 1_000_000,
        "target_net_pct": 2.0,
        "max_hold_seconds": 90,
        "opened_epoch": int(time.time()),
        "mode": "SHADOW",
    }
    result = monitor.evaluate(pos)
    assert result["decision"] == "EXIT" and result["reason"] == "FAST_TAKE_PROFIT", result

    # A profitable peak followed by >1.5% executable reversal exits WARM.
    outputs = iter([1_040_000, 1_020_000])
    monmod.quote_only = lambda *_args, **_kwargs: {"out_amount": next(outputs)}
    monitor = monmod.Stage6Monitor(settings)
    pos["position_id"] = "p-reversal"
    pos["target_net_pct"] = 5.0
    first = monitor.evaluate(pos)
    second = monitor.evaluate(pos)
    assert first["decision"] == "HOLD", first
    assert second["decision"] == "EXIT" and second["reason"] == "WARM_REVERSAL", second

    # Fast adverse move exits without waiting for the old 15% health floor.
    monmod.quote_only = lambda *_args, **_kwargs: {"out_amount": 969_000}
    monitor = monmod.Stage6Monitor(settings)
    pos["position_id"] = "p-stop"
    result = monitor.evaluate(pos)
    assert result["decision"] == "EXIT" and result["reason"] == "FAST_STOP", result

    # Maximum hold remains a hard exit.
    monmod.quote_only = lambda *_args, **_kwargs: {"out_amount": 1_000_000}
    monitor = monmod.Stage6Monitor(settings)
    pos["position_id"] = "p-time"
    pos["opened_epoch"] = int(time.time()) - 100
    pos["max_hold_seconds"] = 90
    result = monitor.evaluate(pos)
    assert result["decision"] == "EXIT" and result["reason"] == "MAX_HOLD_TIME", result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_stage3(root)
        test_stage6(root)
    print("HR-CWH policy regression tests: PASS")
