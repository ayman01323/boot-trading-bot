from __future__ import annotations

from decimal import Decimal, InvalidOperation


def as_decimal(value, default=None):
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def entry_cash_cost(before_native, after_native):
    """Return positive wallet cash outflow for a confirmed entry, otherwise None."""
    before = as_decimal(before_native)
    after = as_decimal(after_native)
    if before is None or after is None:
        return None
    outflow = before - after
    return outflow if outflow > 0 else None


def exit_accounting(
    *,
    remaining_cost_native,
    realised_net_native,
    realised_cost_native,
    sold_raw,
    position_raw_before,
    wallet_cash_change_native,
):
    """Allocate cost basis and calculate realised net P&L for one confirmed exit.

    ``wallet_cash_change_native`` is the native-asset wallet delta measured across
    the whole exit path. It therefore naturally includes execution gas/fees and
    any account-rent refund captured during that same path.
    """
    remaining_cost = as_decimal(remaining_cost_native)
    realised_net = as_decimal(realised_net_native, Decimal(0))
    realised_cost = as_decimal(realised_cost_native, Decimal(0))
    cash_change = as_decimal(wallet_cash_change_native)
    try:
        sold = int(sold_raw)
        raw_before = int(position_raw_before)
    except (TypeError, ValueError):
        return None
    if remaining_cost is None or cash_change is None or sold <= 0 or raw_before <= 0:
        return None

    sold = min(sold, raw_before)
    ratio = Decimal(sold) / Decimal(raw_before)
    cost_basis = remaining_cost * ratio
    net_this = cash_change - cost_basis
    realised_total = realised_net + net_this
    realised_cost_total = realised_cost + cost_basis
    remaining_after = max(Decimal(0), remaining_cost - cost_basis)
    pct_this = (net_this / cost_basis * Decimal(100)) if cost_basis > 0 else None
    pct_total = (
        realised_total / realised_cost_total * Decimal(100)
        if realised_cost_total > 0
        else None
    )
    return {
        "sold_ratio": ratio,
        "cost_basis_native": cost_basis,
        "wallet_cash_change_native": cash_change,
        "net_this_native": net_this,
        "realised_net_total_native": realised_total,
        "realised_cost_total_native": realised_cost_total,
        "remaining_cost_native": remaining_after,
        "net_pct_this": pct_this,
        "net_pct_total": pct_total,
    }
