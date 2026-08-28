from types import SimpleNamespace

from learnerbot import solana_live_patch as live
from learnerbot import solana_sibot as sol
from learnerbot.user_registry import set_user_setting


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def test_trade_size_is_fixed_at_0009_for_default_user(tmp_path):
    app = _app(tmp_path)
    trade, reserve = live.live_limits(
        app,
        "123",
        {"live_trade_sol": "0.0005", "live_min_sol_reserve": "0.02"},
    )
    assert str(trade) == "0.009"
    assert trade == live.LIVE_TRADE_SOL_FIXED
    assert str(reserve) == "0.02"


def test_per_user_trade_override_cannot_shrink_below_fixed_size(tmp_path):
    app = _app(tmp_path)
    tid = "6760898817"
    set_user_setting(app.csv_dir, tid, "solana_live_trade_sol", "0.0005", chain_id=sol.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, tid, "solana_live_min_reserve_sol", "0.005", chain_id=sol.SOLANA_CHAIN_ID)
    trade, reserve = live.live_limits(
        app,
        tid,
        {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02"},
    )
    assert str(trade) == "0.009"
    assert str(reserve) == "0.005"


def test_reserve_override_is_still_honoured_but_not_below_floor(tmp_path):
    app = _app(tmp_path)
    tid = "6760898817"
    set_user_setting(app.csv_dir, tid, "solana_live_min_reserve_sol", "0.001", chain_id=sol.SOLANA_CHAIN_ID)
    trade, floored = live.live_limits(
        app,
        tid,
        {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02"},
    )
    assert str(trade) == "0.009"
    assert str(floored) == "0.005"
