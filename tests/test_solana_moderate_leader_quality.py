from learnerbot import solana_leader_quality_restore_patch as quality
from learnerbot import solana_profit_guard_patch as guard


def test_moderate_leader_quality_profile_values():
    q = quality._QUALITY_FLOOR_OVERRIDES
    assert q["require_complete_history"] == "false"
    assert q["min_win_rate_pct"] == "50"
    assert q["min_profit_factor"] == "1.35"
    assert q["min_recent_win_rate_pct"] == "55"
    assert q["min_recent_profit_factor"] == "1.20"
    assert q["max_leader_drawdown_pct"] == "30"
    assert q["live_min_leader_median_return_pct"] == "2.5"
    assert q["live_min_leader_recent_median_return_pct"] == "2.0"


def test_actual_copied_live_hard_floors_remain_unchanged():
    q = quality._QUALITY_FLOOR_OVERRIDES
    assert q["min_copied_trades_for_guard"] == "2"
    assert q["min_copied_win_rate_pct"] == "50"
    assert q["min_copied_profit_factor"] == "1.50"
    assert q["max_consecutive_copied_losses"] == "2"
    assert q["leader_suspend_minutes"] == "1440"
    assert str(guard._HARD_COPIED_PROFIT_FACTOR) == "1.50"
    assert str(guard._HARD_COPIED_WIN_RATE_PCT) == "50"


def test_settings_overlay_only_changes_policy_keys():
    class Dummy:
        pass

    original = quality._PREV_SETTINGS
    try:
        quality._PREV_SETTINGS = lambda app: {
            "live_order_slippage_bps": "50",
            "live_min_sol_reserve": "0.02",
            "live_max_combined_impact_bps": "500",
            "solana_strategy_profile": "FIRST_DAY",
        }
        cfg = quality.settings_quality_restored(Dummy())
    finally:
        quality._PREV_SETTINGS = original

    assert cfg["live_order_slippage_bps"] == "50"
    assert cfg["live_min_sol_reserve"] == "0.02"
    assert cfg["live_max_combined_impact_bps"] == "500"
    assert cfg["min_profit_factor"] == "1.35"
    assert cfg["min_recent_win_rate_pct"] == "55"
    assert cfg["solana_strategy_profile"].endswith("+MODERATE_LEADER_QUALITY")
