from __future__ import annotations

from contextlib import closing
from decimal import Decimal, ROUND_CEILING

from . import solana_execution_efficiency_patch as _efficiency
from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_profit_first_live_correction_patch as _profit_first
from . import solana_refundable_rent_accounting_patch as _rent
from . import solana_sibot as _sol

# Full leader exits remain immediate risk-control events. A PARTIAL leader sell is
# optional profit-taking and must never be used where fixed Solana costs dominate
# the follower slice. Low-capital positions therefore HOLD partial leader exits.
_HARD_MIN_PARTIAL_NET_PCT = Decimal("3.0")
_HARD_MIN_POSITION_ECONOMIC_VALUE_SOL = Decimal("0.002")

_sol.DEFAULTS.update({
    "live_min_partial_exit_net_pct": (
        "3.0",
        "Minimum economically reconciled net profit percent required before copying a leader partial SELL",
    ),
    "live_min_position_economic_value_for_partial_sell_sol": (
        "0.002",
        "Minimum non-rent economic position value before any leader partial SELL is allowed",
    ),
})

_PREV_PROCESS = _sol.process_leader_event


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def minimum_economic_partial_value_lamports(cfg: dict) -> int:
    """Minimum partial proceeds whose base fee fits inside the configured fee ratio."""
    ratio_pct = max(Decimal("0.01"), _d(cfg.get("live_max_fee_ratio_pct"), "1.2"))
    value = Decimal(_efficiency.DEFAULT_BASE_FEE_LAMPORTS) * Decimal(100) / ratio_pct
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def position_economic_value_sol(app, position: dict) -> Decimal:
    """Return actual trading principal, excluding refundable token-account rent."""
    cash_cost = max(Decimal(0), _d((position or {}).get("entry_cost_sol"), 0))
    try:
        rent = max(Decimal(0), _rent._rent_principal_sol(app, (position or {}).get("position_id")))
    except Exception:
        rent = Decimal(0)
    return max(Decimal(0), cash_cost - rent)


def _partial_skip(tid: str, position: dict, reason: str, *, net_pct=None, proceeds_lamports=None) -> dict:
    row = {
        "telegram_id": str(tid),
        "action": "SKIP_PARTIAL_SELL",
        "position_id": str(position.get("position_id") or ""),
        "reason": str(reason),
    }
    if net_pct is not None:
        row["net_pct"] = str(net_pct)
    if proceeds_lamports is not None:
        row["estimated_proceeds_lamports"] = int(proceeds_lamports)
    return row


def process_leader_event_partial_profit_guard(app, event: dict):
    """Profit/size gate leader partial sells; delegate BUYs and full SELLs unchanged."""
    action = str((event or {}).get("action") or "").upper()
    sell_pct = _sol._float((event or {}).get("sell_pct"), 100)
    if action != "SELL" or sell_pct >= 99:
        return _PREV_PROCESS(app, event)

    cfg = _sol.settings(app)
    if not _sol._bool(cfg.get("mirror_partial_sells"), True):
        return []

    # Hard floors deliberately cannot be relaxed by CSV or the hourly optimiser.
    minimum_net_pct = max(
        _HARD_MIN_PARTIAL_NET_PCT,
        max(Decimal("0"), _d(cfg.get("live_min_partial_exit_net_pct"), "3.0")),
    )
    minimum_position_value = max(
        _HARD_MIN_POSITION_ECONOMIC_VALUE_SOL,
        max(Decimal("0"), _d(cfg.get("live_min_position_economic_value_for_partial_sell_sol"), "0.002")),
    )
    minimum_value_lamports = minimum_economic_partial_value_lamports(cfg)
    fraction = max(Decimal("0.0001"), min(Decimal(1), _d(sell_pct, 100) / Decimal(100)))
    actions = []

    for user in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        if _sol._leader_rank(app, tid, event["leader_wallet"]) is None:
            continue

        with closing(_sol.connect(app)) as conn:
            positions = [dict(row) for row in conn.execute(
                "SELECT * FROM positions WHERE telegram_id=? AND leader_wallet=? AND mint=? "
                "AND status='OPEN' AND mode='LIVE'",
                (tid, event["leader_wallet"], event["mint"]),
            ).fetchall()]

        for position in positions:
            economic_position_value = position_economic_value_sol(app, position)
            if economic_position_value < minimum_position_value:
                actions.append(_partial_skip(
                    tid,
                    position,
                    f"low-capital HOLD: economic position value {economic_position_value:.9f} SOL is below "
                    f"partial-exit floor {minimum_position_value:.9f} SOL",
                ))
                continue

            # Use the same rent-aware/economic valuation as LIVE monitoring. If we
            # cannot prove the partial slice is sufficiently profitable, fail closed.
            try:
                valuation = dict(_sol.evaluate_position(app, position, fraction) or {})
                net_pct = _d(valuation.get("net_pct"), "-999")
                proceeds_sol = max(Decimal(0), _d(valuation.get("proceeds_sol"), 0))
                proceeds_lamports = int((proceeds_sol * Decimal(1_000_000_000)).to_integral_value())
            except Exception as exc:
                actions.append(_partial_skip(
                    tid,
                    position,
                    f"partial valuation unavailable: {type(exc).__name__}: {str(exc)[:250]}",
                ))
                continue

            if net_pct < minimum_net_pct:
                actions.append(_partial_skip(
                    tid,
                    position,
                    f"partial follower slice net {net_pct:.4f}% is below required +{minimum_net_pct:.4f}%",
                    net_pct=net_pct,
                    proceeds_lamports=proceeds_lamports,
                ))
                continue

            if proceeds_lamports < minimum_value_lamports:
                actions.append(_partial_skip(
                    tid,
                    position,
                    "partial value is too small for one Solana base fee to fit inside the configured fee ratio",
                    net_pct=net_pct,
                    proceeds_lamports=proceeds_lamports,
                ))
                continue

            claimed, attempt_key = _live._claim_attempt(app, tid, event)
            if not claimed:
                actions.append({
                    "telegram_id": tid,
                    "action": "SKIP",
                    "reason": "duplicate leader partial SELL signal already attempted",
                })
                continue

            reason = "SOLANA_LEADER_PARTIAL_SELL_PROFIT_GATED"
            try:
                result = _live._close_live(app, tid, position, fraction, reason)
                realised_net = _d(result.get("net_sol"), 0)
                _live._update_attempt(app, attempt_key, "EXECUTED", result.get("trade"))
                actions.append({
                    "telegram_id": tid,
                    "action": "SELL",
                    "position_id": position.get("position_id"),
                    "signature": result.get("signature"),
                    "reason": reason,
                    "net_sol": str(realised_net),
                    "pretrade_net_pct": str(net_pct),
                    "posttrade_result": "PROFIT" if realised_net > 0 else "NON_PROFIT",
                })
            except _exec.SolanaLivePostExecutionError as exc:
                _live._update_attempt(app, attempt_key, "LANDED_INVALID_OUTPUT", exc.result, str(exc))
                _live._record_execution_fault(app, tid, cfg, exc)
                actions.append({
                    "telegram_id": tid,
                    "action": "REJECT",
                    "reason": str(exc),
                    "signature": exc.signature,
                })
            except Exception as exc:
                _live._update_attempt(app, attempt_key, "FAILED_NO_RETRY", None, str(exc))
                actions.append(_partial_skip(
                    tid,
                    position,
                    f"guarded partial execution blocked: {type(exc).__name__}: {str(exc)[:250]}",
                    net_pct=net_pct,
                    proceeds_lamports=proceeds_lamports,
                ))

    return actions


def install():
    if getattr(_sol, "_partial_sell_profit_guard_installed", False):
        return
    _sol.process_leader_event = process_leader_event_partial_profit_guard
    _sol._partial_sell_profit_guard_installed = True
    print(
        "[solana-partial-sell-profit-guard] full_exit=immediate "
        "partial_exit=disabled_below_0.002_sol_and_profit_gated min_net=3%"
    )


install()
