from types import SimpleNamespace

from learnerbot import solana_live_patch as live
from learnerbot import solana_sibot as sol
from learnerbot.user_registry import set_user_setting


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def test_default_user_keeps_platform_reserve(tmp_path):
    app = _app(tmp_path)
    trade, reserve = live.live_limits(
        app,
        "123",
        {"live_trade_sol": "0.0005", "live_min_sol_reserve": "0.02"},
    )
    assert str(trade) == "0.0005"
    assert str(reserve) == "0.02"


def test_user_specific_low_capital_reserve_is_allowed_but_not_below_floor(tmp_path):
    app = _app(tmp_path)
    tid = "6760898817"
    set_user_setting(app.csv_dir, tid, "solana_live_trade_sol", "0.0005", chain_id=sol.SOLANA_CHAIN_ID)
    set_user_setting(app.csv_dir, tid, "solana_live_min_reserve_sol", "0.005", chain_id=sol.SOLANA_CHAIN_ID)
    trade, reserve = live.live_limits(
        app,
        tid,
        {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02"},
    )
    assert str(trade) == "0.0005"
    assert str(reserve) == "0.005"
    assert trade + reserve == live.Decimal("0.0055")

    set_user_setting(app.csv_dir, tid, "solana_live_min_reserve_sol", "0.001", chain_id=sol.SOLANA_CHAIN_ID)
    _, floored = live.live_limits(
        app,
        tid,
        {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02"},
    )
    assert str(floored) == "0.005"
