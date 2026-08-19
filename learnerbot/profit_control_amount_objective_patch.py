from __future__ import annotations

from decimal import Decimal

from . import profit_control_loop_patch as _control

# A strategy is not considered successful merely because it wins more often.
# Realised gross-profit amount must exceed realised gross-loss amount by 30%.
MIN_AMOUNT_PROFIT_FACTOR = Decimal("1.30")
_PREV_IS_SUCCESS = _control._is_success


def is_success_amount_first(
    wins: int,
    losses: int,
    net: Decimal,
    profit_factor: Decimal,
    *,
    min_trades: int,
    closed: int,
) -> bool:
    # profit_factor = gross_profit / gross_loss, so this is explicitly an amount
    # test. Win/loss count remains diagnostic but does not override profitable
    # economics: a smaller number of sufficiently large wins may be successful.
    return (
        int(closed) >= int(min_trades)
        and Decimal(net) > 0
        and Decimal(profit_factor) >= MIN_AMOUNT_PROFIT_FACTOR
    )


def install():
    if getattr(_control, "_amount_profit_objective_installed", False):
        return
    _control.MIN_SUCCESS_PROFIT_FACTOR = MIN_AMOUNT_PROFIT_FACTOR
    _control._is_success = is_success_amount_first
    _control._amount_profit_objective_installed = True
    print("[profit-control-amount-objective] realised_profit_amount>=1.30x_realised_loss_amount")


install()
