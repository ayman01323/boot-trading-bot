from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import profit_control_loop_patch as _control
from . import solana_execution_efficiency_patch as _efficiency
from . import solana_exit_circuit_breaker_patch as _exit_circuit  # noqa: F401
from . import solana_liquidity_fail_closed_patch  # noqa: F401
from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

# The hourly optimiser remains free to choose among its reviewed profiles, but
# these limits are the non-relaxable LIVE floor after the observed all-loss exit
# pattern. They affect selection/timing/exits only; capital, reserve, signing,
# simulation and LIVE/ARMED state are untouched.
_PREV_SETTINGS = _sol.settings
_PREV_PROCESS = _sol.process_leader_event


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def _i(value, default=0) -> int:
    return _sol._int(value, default)


def _min_dec(cfg: dict, key: str, ceiling: str) -> None:
    cfg[key] = str(min(_d(cfg.get(key), ceiling), Decimal(ceiling)))


def _max_dec(cfg: dict, key: str, floor: str) -> None:
    cfg[key] = str(max(_d(cfg.get(key), floor), Decimal(floor)))


def _min_int(cfg: dict, key: str, ceiling: int) -> None:
    cfg[key] = str(min(_i(cfg.get(key), ceiling), int(ceiling)))


def _max_int(cfg: dict, key: str, floor: int) -> None:
    cfg[key] = str(max(_i(cfg.get(key), floor), int(floor)))


def settings_profit_first_live(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))

    # Fresh, high-quality entries only. A late copied entry can turn a profitable
    # leader trade into a losing follower trade even when the leader itself wins.
    _max_dec(cfg, "min_win_rate_pct", "60")
    _max_dec(cfg, "min_profit_factor", "1.50")
    _max_dec(cfg, "min_recent_win_rate_pct", "60")
    _max_dec(cfg, "min_recent_profit_factor", "1.25")
    _min_int(cfg, "max_signal_age_seconds", 10)
    _min_dec(cfg, "max_roundtrip_loss_pct", "1.0")
    _min_dec(cfg, "max_entry_deterioration_pct", "0.25")

    # After two actual LIVE copies there must already be evidence that this leader
    # works for OUR timing/cost structure, not only in reconstructed leader history.
    _min_int(cfg, "min_copied_trades_for_guard", 2)
    _max_dec(cfg, "min_copied_win_rate_pct", "50")
    _max_dec(cfg, "min_copied_profit_factor", "1.20")
    _min_int(cfg, "max_consecutive_copied_losses", 2)
    _max_int(cfg, "leader_suspend_minutes", 360)

    # Earlier net-profit protection. Values are percentages of the copied
    # position's economically reconciled P&L, after refundable rent treatment.
    _min_dec(cfg, "break_even_trigger_pct", "3")
    _max_dec(cfg, "break_even_floor_pct", "0.25")
    _min_dec(cfg, "trailing_trigger_pct", "5")
    _min_dec(cfg, "trailing_gap_pct", "2")
    _min_dec(cfg, "take_profit_pct", "10")
    _min_dec(cfg, "stop_loss_pct", "5")
    # Old EXIT_PENDING rows no longer wait for -2.5%; once non-positive they are
    # eligible for the normal monitor exit. New leader exits are attempted at once.
    _min_dec(cfg, "leader_exit_loss_cap_pct", "0")

    # A 0.0005 SOL canary has only 500,000 lamports of principal. With the Solana
    # base signature fee near 5,000 lamports, there is almost no economic room for
    # priority/MEV spend. 1.2% gives a 6,000-lamport total cap at this trade size.
    _min_int(cfg, "live_max_total_fee_lamports", 60_000)
    _min_dec(cfg, "live_max_fee_ratio_pct", "1.2")
    _min_dec(cfg, "live_expected_profit_margin_pct", "6")
    _min_dec(cfg, "live_max_fee_share_of_expected_profit_pct", "20")
    cfg["live_enable_jito_tip"] = "false"
    cfg["live_max_jito_tip_lamports"] = "0"

    # Liquidity/route quality must leave room for a positive move after execution.
    _min_int(cfg, "live_order_slippage_bps", 30)
    _min_dec(cfg, "live_max_combined_impact_slippage_bps", "100")
    _min_dec(cfg, "live_multihop_max_combined_bps", "75")
    _min_dec(cfg, "live_atomic_route_deterioration_bps", "25")
    cfg["live_require_price_impact_quote"] = "true"
    cfg["profit_first_live_correction"] = "true"
    return cfg


def _mark_pending(app, position_id: str, reason: str) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            conn.execute(
                "UPDATE positions SET leader_exit_pending=1,exit_reason=?,updated_at=? "
                "WHERE position_id=? AND status='OPEN'",
                (str(reason)[:450], int(time.time()), str(position_id)),
            )
            conn.commit()
    except Exception:
        pass


def process_leader_event_profit_first(app, event: dict):
    """Copy leader SELL immediately instead of waiting for follower loss to deepen.

    BUY processing remains the audited existing implementation, now receiving the
    stricter settings above. SELL keeps the same leader membership, per-user LIVE
    gate, durable attempt key, wallet-bound close, economic fee/liquidity guard,
    atomic full-close path and landed-invalid circuit breaker.
    """
    if str((event or {}).get("action") or "").upper() != "SELL":
        return _PREV_PROCESS(app, event)

    cfg = _sol.settings(app)
    actions = []
    for user in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        rank = _sol._leader_rank(app, tid, event["leader_wallet"])
        if rank is None:
            continue

        with closing(_sol.connect(app)) as conn:
            positions = [dict(row) for row in conn.execute(
                "SELECT * FROM positions WHERE telegram_id=? AND leader_wallet=? AND mint=? "
                "AND status='OPEN' AND mode='LIVE'",
                (tid, event["leader_wallet"], event["mint"]),
            ).fetchall()]

        for position in positions:
            full = _sol._float(event.get("sell_pct"), 100) >= 99
            fraction = Decimal(1) if full else max(
                Decimal("0.0001"),
                min(Decimal(1), _d(event.get("sell_pct"), 100) / Decimal(100)),
            )
            if not full and not _sol._bool(cfg.get("mirror_partial_sells"), True):
                continue

            claimed, attempt_key = _live._claim_attempt(app, tid, event)
            if not claimed:
                actions.append({
                    "telegram_id": tid,
                    "action": "SKIP",
                    "reason": "duplicate leader SELL signal already attempted",
                })
                continue

            reason = "SOLANA_LEADER_SELL_IMMEDIATE" if full else "SOLANA_LEADER_PARTIAL_SELL_IMMEDIATE"
            try:
                result = _live._close_live(app, tid, position, fraction, reason)
                _live._update_attempt(app, attempt_key, "EXECUTED", result.get("trade"))
                actions.append({
                    "telegram_id": tid,
                    "action": "SELL",
                    "position_id": position["position_id"],
                    "signature": result.get("signature"),
                    "reason": reason,
                    "net_sol": str(result.get("net_sol") or ""),
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
                # If fee/liquidity/congestion blocks the immediate attempt, preserve
                # exit intent for the monitored path; never submit an uneconomic tx.
                _live._update_attempt(app, attempt_key, "FAILED_NO_RETRY", None, str(exc))
                _mark_pending(app, position["position_id"], "LEADER_EXIT_BLOCKED: " + str(exc))
                _live._notify(
                    app,
                    tid,
                    "⚠️ <b>Leader exit blocked before execution</b>\n"
                    f"<code>{type(exc).__name__}: {str(exc)[:500]}</code>\n"
                    "The position remains marked for exit; the bot will not bypass fee/liquidity safety limits.",
                )
                actions.append({
                    "telegram_id": tid,
                    "action": "EXIT_PENDING",
                    "position_id": position["position_id"],
                    "reason": str(exc),
                })
    return actions


def install():
    if getattr(_sol, "_profit_first_live_correction_installed", False):
        return

    # Tighten the existing hourly control loop without giving it access to capital
    # or safety switches. These values are effective floors even when a profile or
    # persisted CSV contains looser historic settings.
    _sol.settings = settings_profit_first_live
    _sol.process_leader_event = process_leader_event_profit_first

    # Keep the profit-control module aware that its settings wrapper is now inside
    # a stricter outer policy layer. No strategy profile can relax these floors.
    _control.HARD_LIVE_POLICY_WRAPPER = "solana_profit_first_live_correction_patch"
    _sol._profit_first_live_correction_installed = True
    print(
        "[solana-profit-first-live] signal_age<=10s entry_deterioration<=0.25% "
        "roundtrip_loss<=1% fee_ratio<=1.2% leader_sell=immediate "
        "copied_guard_after=2 break_even=3% trailing=5% take_profit=10%"
    )


install()
