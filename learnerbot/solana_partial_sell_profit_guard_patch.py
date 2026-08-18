from __future__ import annotations

from contextlib import closing
from decimal import Decimal, ROUND_CEILING

from . import solana_execution_efficiency_patch as _efficiency
from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_profit_first_live_correction_patch as _profit_first
from . import solana_sibot as _sol

# Full leader exits remain immediate risk-control events. A PARTIAL leader sell is
# different: it is optional profit-taking and must not realise a negative follower
# slice merely because the leader entered earlier at a better price.
_sol.DEFAULTS.update({
    "live_min_partial_exit_net_pct": (
        "1.0",
        "Minimum economically reconciled net profit percent required before copying a leader partial SELL",
    ),
})

_PREV_PROCESS = _sol.process_leader_event


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def minimum_economic_partial_value_lamports(cfg: dict) -> int:
    """Minimum partial proceeds whose base fee fits inside the configured fee ratio.

    At a 1.2% fee ratio and a 5,000-lamport base fee, a partial exit must be worth
    at least 416,667 lamports (~0.000416667 SOL) before it can be economical even
    with zero priority fee. Smaller partials are held for a later profitable/full exit.
    """
    ratio_pct = max(Decimal("0.01"), _d(cfg.get("live_max_fee_ratio_pct"), "1.2"))
    value = Decimal(_efficiency.DEFAULT_BASE_FEE_LAMPORTS) * Decimal(100) / ratio_pct
    return int(value.to_integral_value(rounding=ROUND_CEILING))


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

    minimum_net_pct = max(Decimal("0"), _d(cfg.get("live_min_partial_exit_net_pct"), "1.0"))
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
            # Use the same rent-aware/economic valuation as LIVE monitoring. If we
            # cannot prove the partial slice is profitable, fail closed and hold it.
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

            # Only now consume the durable leader-signature attempt. A skipped
            # partial never submits a chain transaction and never converts the
            # position into EXIT_PENDING.
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
                _live._update_attempt(app, attempt_key, "EXECUTED", result.get("trade"))
                actions.append({
                    "telegram_id": tid,
                    "action": "SELL",
                    "position_id": position.get("position_id"),
                    "signature": result.get("signature"),
                    "reason": reason,
                    "net_sol": str(result.get("net_sol") or ""),
                    "pretrade_net_pct": str(net_pct),
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
                # A partial sale is optional. If congestion/liquidity/fee protection
                # blocks it, hold the position rather than flagging the whole trade
                # for a forced exit. A later full leader SELL still executes through
                # the immediate risk-control path.
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
        "partial_exit=net_profit_and_fee_size_gated min_net=1%"
    )


install()
