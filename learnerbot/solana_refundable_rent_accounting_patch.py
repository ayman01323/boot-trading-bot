from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol
from . import solana_token_account_reclaim_patch as _reclaim

_PREV_EVALUATE = _sol.evaluate_position


def _rent_principal_sol(app, position_id) -> Decimal:
    """Rent principal still tied up in bot-created token accounts for this position."""
    try:
        rows = _reclaim._tracked_accounts(app, position_id)
    except Exception:
        return Decimal(0)
    lamports = 0
    for row in rows:
        try:
            lamports += max(0, int(row.get("entry_lamports") or 0))
        except Exception:
            continue
    return Decimal(lamports) / Decimal(1_000_000_000)


def evaluate_position_economic(app, position: dict, fraction=Decimal(1)):
    """Value token performance without misclassifying refundable rent as trading loss."""
    p = dict(position or {})
    if str(p.get("mode") or "").upper() == "LIVE":
        rent = _rent_principal_sol(app, p.get("position_id"))
        cash_cost = _sol._dec(p.get("entry_cost_sol"), 0)
        economic_cost = max(Decimal(0), cash_cost - rent)
        if economic_cost > 0:
            p["entry_cost_sol"] = str(economic_cost)
    result = dict(_PREV_EVALUATE(app, p, fraction) or {})
    result["refundable_rent_sol"] = _rent_principal_sol(app, p.get("position_id")) if str(p.get("mode") or "").upper() == "LIVE" else Decimal(0)
    return result


def _close_live_rent_aware(app, tid, position, fraction: Decimal, reason: str):
    executor, actual = _binding._resolve_executor(app, tid, position)
    old_raw = max(1, _sol._int(position.get("token_amount_raw"), 0))
    f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    planned = max(1, int(Decimal(old_raw) * f))
    sell_raw = min(planned, int(actual))
    if sell_raw <= 0:
        _binding._quarantine(app, position, "resolved wallet has no sellable matching token")

    rent_principal = _rent_principal_sol(app, position.get("position_id"))
    old_cash_cost = _sol._dec(position.get("entry_cost_sol"), 0)
    old_economic_cost = max(Decimal(0), old_cash_cost - rent_principal)

    trade = executor.sell(position["mint"], sell_raw)
    out_lamports = max(0, int(trade.get("totalOutputAmount") or trade.get("outputAmountResult") or 0))
    gross_swap_output = Decimal(out_lamports) / Decimal(1_000_000_000)

    # Keep gross router output separate from actual wallet cashflow.  A swap output
    # cannot be negative; a negative wallet delta means execution fees/other tx-level
    # costs exceeded the SOL delivered to the wallet and must be shown as cashflow,
    # not mislabeled as negative "swap proceeds".
    delta_raw = trade.get("wallet_delta_lamports")
    delta_known = delta_raw is not None
    try:
        delta = int(delta_raw) if delta_known else 0
    except Exception:
        delta_known = False
        delta = 0

    cfg = _sol.settings(app)
    if delta_known:
        wallet_cashflow = Decimal(delta) / Decimal(1_000_000_000)
    else:
        wallet_cashflow = gross_swap_output - _sol._dec(cfg.get("estimated_exit_fee_sol"), ".00002")

    remaining = max(0, old_raw - sell_raw)
    closed = remaining <= max(1, int(old_raw * .001)) or f >= Decimal("0.999")

    reclaim = {"reclaimed_lamports": 0, "signature": "", "accounts": []}
    if closed:
        try:
            reclaim = _reclaim._close_created_empty_accounts(
                executor,
                str(position.get("position_id") or ""),
                str(position.get("mint") or ""),
            )
        except Exception as exc:
            print("[solana-token-reclaim]", type(exc).__name__, exc)

    reclaimed_sol = Decimal(int(reclaim.get("reclaimed_lamports") or 0)) / Decimal(1_000_000_000)
    if closed:
        # For managed/non-atomic exits, a separately reclaimed account is added once.
        # Atomic exits already include rent in wallet_delta and normally report no
        # second reclaim here, avoiding double counting.
        proceeds = wallet_cashflow + reclaimed_sol
        net = proceeds - old_cash_cost
        remaining_cost = Decimal(0)
    else:
        # A partial sell does not consume or refund the fixed token-account rent.
        sold_fraction = Decimal(sell_raw) / Decimal(old_raw)
        economic_cost_sold = old_economic_cost * sold_fraction
        net = wallet_cashflow - economic_cost_sold
        remaining_economic_cost = max(Decimal(0), old_economic_cost - economic_cost_sold)
        remaining_cost = remaining_economic_cost + rent_principal
        proceeds = wallet_cashflow

    realised = _sol._dec(position.get("realised_net_sol"), 0) + net
    now = int(time.time())
    sig = str(trade.get("signature") or "")
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,realised_net_sol=?,exit_signature=?,exit_reason=?,closed_at=?,
                                    status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
            (
                str(0 if closed else remaining),
                str(0 if closed else remaining_cost),
                str(realised),
                sig,
                reason,
                now if closed else None,
                "CLOSED" if closed else "OPEN",
                0 if closed else int(position.get("leader_exit_pending") or 0),
                now,
                position["position_id"],
            ),
        )
        conn.commit()

    reclaim_line = f"Rent reclaimed: <b>{reclaimed_sol:.9f} SOL</b>\n" if reclaimed_sol > 0 else ""
    partial_line = (
        f"Refundable rent still reserved: <b>{rent_principal:.9f} SOL</b>\n"
        if not closed and rent_principal > 0 else ""
    )
    _live._notify(
        app,
        tid,
        f"✅ <b>Solana LIVE SELL confirmed</b>\n"
        f"Reason: <code>{reason}</code>\n"
        f"Wallet: <code>{executor.address[:8]}…{executor.address[-6:]}</code>\n"
        f"Gross swap output: <b>{gross_swap_output:.9f} SOL</b>\n"
        f"Wallet cashflow after transaction: <b>{wallet_cashflow:+.9f} SOL</b>\n"
        f"{reclaim_line}{partial_line}"
        f"Realised net P&L: <b>{net:+.9f} SOL</b>\n"
        f"TX: <code>{sig}</code>"
        + (f"\nRent reclaim TX: <code>{reclaim.get('signature')}</code>" if reclaim.get("signature") else ""),
    )
    trade = dict(trade or {})
    trade["gross_swap_output_lamports"] = int(out_lamports)
    trade["wallet_cashflow_lamports"] = int(delta) if delta_known else int((wallet_cashflow * Decimal(1_000_000_000)).to_integral_value())
    trade["rent_reclaimed_lamports"] = int(reclaim.get("reclaimed_lamports") or 0)
    trade["rent_reclaim_signature"] = str(reclaim.get("signature") or "")
    return {
        "closed": closed,
        "net_sol": net,
        "signature": sig,
        "reason": reason,
        "trade": trade,
        "gross_swap_output_sol": gross_swap_output,
        "wallet_cashflow_sol": wallet_cashflow,
        "rent_reclaimed_sol": reclaimed_sol,
        "refundable_rent_sol": rent_principal,
    }


def retry_pending_rent_reclaims(app, limit=5):
    """Best-effort recovery for a successful SELL whose separate rent-close tx failed."""
    with closing(_sol.connect(app)) as conn:
        _reclaim._ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            """SELECT DISTINCT p.position_id,p.telegram_id,p.mint,p.realised_net_sol
               FROM positions p
               JOIN live_position_created_token_accounts a ON a.position_id=p.position_id
               WHERE p.mode='LIVE' AND p.status='CLOSED' AND a.closed_at IS NULL
               ORDER BY p.closed_at LIMIT ?""",
            (max(1, min(20, int(limit))),),
        ).fetchall()]
    for p in rows:
        binding = _binding._binding(app, p["position_id"])
        if not binding:
            continue
        try:
            executor = _binding._exec.SolanaLiveExecutor(
                app, p["telegram_id"], wallet_id=binding.get("wallet_id")
            )
            result = _reclaim._close_created_empty_accounts(
                executor, p["position_id"], p["mint"]
            )
            reclaimed = Decimal(int(result.get("reclaimed_lamports") or 0)) / Decimal(1_000_000_000)
            if reclaimed <= 0:
                continue
            with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
                row = conn.execute(
                    "SELECT realised_net_sol FROM positions WHERE position_id=?",
                    (p["position_id"],),
                ).fetchone()
                current = _sol._dec(row["realised_net_sol"], 0) if row else Decimal(0)
                corrected = current + reclaimed
                conn.execute(
                    "UPDATE positions SET realised_net_sol=?,updated_at=? WHERE position_id=?",
                    (str(corrected), int(time.time()), p["position_id"]),
                )
                conn.commit()
            _live._notify(
                app,
                p["telegram_id"],
                f"♻️ <b>Solana rent recovery completed</b>\n"
                f"Recovered: <b>{reclaimed:.9f} SOL</b>\n"
                f"Corrected cumulative realised P&L: <b>{corrected:+.9f} SOL</b>\n"
                f"TX: <code>{result.get('signature') or ''}</code>",
            )
        except Exception as exc:
            print("[solana-rent-retry]", type(exc).__name__, exc)


def install():
    _sol.evaluate_position = evaluate_position_economic
    _binding._close_bound_live = _close_live_rent_aware
    _live._close_live = _close_live_rent_aware
    _sol.retry_pending_rent_reclaims = retry_pending_rent_reclaims
    print(
        "[solana-rent-accounting] open_pnl_excludes_refundable_rent=true "
        "partial_keeps_rent=true gross_output_separate_from_wallet_cashflow=true"
    )


install()
