from decimal import Decimal

from learnerbot.sibot_reasonable_top20_patch import _is_leader_candidate, _is_top20_candidate


def row(net, profit, loss, closed=1, win_rate=100.0):
    return {
        "net": Decimal(str(net)),
        "profit": Decimal(str(profit)),
        "loss": Decimal(str(loss)),
        "closed": closed,
        "win_rate": win_rate,
    }


def test_top20_is_profit_first_not_50_trade_gated():
    a = row("0.25", "0.40", "0.15", closed=2, win_rate=50)
    assert _is_top20_candidate(a) is True
    assert _is_leader_candidate(a, min_closed=5, min_win_rate=50) is False


def test_loss_making_wallet_never_enters_top20():
    assert _is_top20_candidate(row("-0.01", "0.20", "0.21", closed=40, win_rate=80)) is False


def test_reasonable_default_leader_threshold():
    a = row("1.0", "1.4", "0.4", closed=5, win_rate=60)
    assert _is_top20_candidate(a) is True
    assert _is_leader_candidate(a, min_closed=5, min_win_rate=50) is True
