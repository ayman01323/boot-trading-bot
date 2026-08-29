from __future__ import annotations

"""Owner-approved Learner/Solana Change Set 4.

Approval timestamp: 2026-08-29T10:38:58Z (2026-08-29 11:38:58 BST)
Subject: restore historical 12:30 BUY profile; 0.005 SOL / 10 LIVE positions;
30+3 minute, +15% full-exit policy; LP-unlocked requires revalidation instead
of being an automatic LIVE refusal.

This layer is intentionally late-bound.  It changes strategy/capacity policy only
and keeps the already-audited signer, simulation, reserve, quote, slippage,
transaction-validation, wallet-binding, kill-switch and protected close paths.
"""

import time
from contextlib import closing
from decimal import Decimal

from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_patch as _live
from . import solana_pool_risk_gate as _pool
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol

CHANGESET_ID = "CHANGE_SET_4"
CHANGESET_APPROVED_UTC = "2026-08-29T10:38:58Z"
CHANGESET_APPROVED_BST = "2026-08-29T11:38:58+01:00"
CHANGESET_SUBJECT = (
    "Learner historical BUY restore + 0.005 SOL/10 positions + "
    "30+3 minute full-exit + LP conditional revalidation"
)
OWNER_LIVE_TRADE_SOL = Decimal("0.005")
OWNER_MAX_LIVE_POSITIONS = 10
OWNER_FORCE_EXIT_SECONDS = 33 * 60

_PREV_LIVE_LIMITS = _live.live_limits
_PREV_RUGCHECK = _pool.evaluate_rugcheck
_BASE_LIVE_PROCESS = _pool._PREV_PROCESS
_PREV_MONITOR = _sol.monitor_positions


def live_limits_owner_changeset_4(app, telegram_id, cfg=None):
    """Pin the approved BUY size to exactly 0.005 SOL; preserve reserve logic."""
    _trade, reserve = _PREV_LIVE_LIMITS(app, telegram_id, cfg)
    return OWNER_LIVE_TRADE_SOL, reserve


def evaluate_rugcheck_lp_revalidation(summary: dict, cfg: dict) -> dict:
    """LP-unlocked/low-lock is a revalidation trigger, not a standalone refusal.

    Structural RugCheck failures remain HARD_BLOCK.  Only the former
    LP_CONCENTRATION_RISK result is changed to PASS-with-revalidation, which means
    the existing DexScreener checks and fresh Jupiter reverse-depth probe still
    have to pass before LIVE can proceed.
    """
    result = dict(_PREV_RUGCHECK(summary, cfg) or {})
    if str(result.get("reason_code") or "") != "LP_CONCENTRATION_RISK":
        return result
    evidence = dict(result.get("evidence") or {})
    evidence["lp_revalidation_required"] = True
    evidence["changeset_id"] = CHANGESET_ID
    return _pool._decision(
        "PASS",
        "LP_REVALIDATION_REQUIRED",
        "LP lock/concentration is not a standalone LIVE refusal; require current Dex pool checks and fresh reverse-depth sellability before entry",
        evidence,
    )


def eligible_live_users_owner_changeset_4(app, event: dict, cfg: dict):
    """Same canonical eligibility logic, with the approved 10-position ceiling."""
    out = []
    for user in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        if _sol._leader_rank(app, tid, event.get("leader_wallet")) is None:
            continue
        limit = max(1, min(OWNER_MAX_LIVE_POSITIONS, _sol._int(cfg.get("live_max_positions"), 1)))
        if _live._open_live_count(app, tid) >= limit or _sol._open_position(app, tid, event.get("mint")):
            continue
        allocation, _ = _live.live_limits(app, tid, cfg)
        out.append((tid, Decimal(str(allocation))))
    return out


def process_leader_event_owner_changeset_4(app, event: dict):
    """Canonical LIVE process with only the hard position ceiling raised 5 -> 10.

    SELL handling is delegated unchanged to the pre-PoolCheck LIVE implementation;
    the approved no-routine-partial policy is supplied through settings and the
    33-minute full-exit fallback is supplied by the monitor wrapper below.
    """
    if str((event or {}).get("action") or "").upper() != "BUY":
        return _BASE_LIVE_PROCESS(app, event)

    cfg = _sol.settings(app)
    actions = []
    for u in _live.all_users(app.csv_dir, enabled_only=True):
        tid = str(u.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        if not _sol._sibot._bool(_sol._sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        rank = _sol._leader_rank(app, tid, event["leader_wallet"])
        if rank is None:
            continue

        max_positions = max(
            1,
            min(OWNER_MAX_LIVE_POSITIONS, _sol._int(cfg.get("live_max_positions"), 1)),
        )
        if _live._open_live_count(app, tid) >= max_positions or _sol._open_position(app, tid, event["mint"]):
            actions.append({"telegram_id": tid, "action": "SKIP", "reason": "LIVE position limit/already held"})
            continue

        allocation, reserve = _live.live_limits(app, tid, cfg)
        try:
            ok, reason, _ = _sol._validate_shadow_entry(app, event, allocation, cfg)
            if not ok:
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                continue
            ok, reason = _live._economic_entry_gate(app, tid, allocation, cfg)
            if not ok:
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                continue

            executor = _live.SolanaLiveExecutor(app, tid)
            need = int((allocation + reserve) * Decimal(1_000_000_000))
            if executor.native_balance_lamports() < need:
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": "insufficient SOL for trade plus reserve"})
                continue

            claimed, attempt_key = _live._claim_attempt(app, tid, event)
            if not claimed:
                actions.append({"telegram_id": tid, "action": "SKIP", "reason": "duplicate leader signal already attempted"})
                continue
            try:
                trade = executor.buy(event["mint"], allocation, reserve)
                _live._update_attempt(app, attempt_key, "EXECUTED", trade)
                pid, out_raw, entry_cost = _live._insert_live_position(
                    app, tid, rank, event, trade, allocation, cfg
                )
                sig = str(trade.get("signature") or "")
                _live._notify(
                    app,
                    tid,
                    f"🚀 <b>Solana LIVE BUY confirmed</b>\n"
                    f"Actual wallet spend: <b>{entry_cost:.9f} SOL</b>\n"
                    f"Token: <code>{event['mint']}</code>\n"
                    f"Received raw: <code>{out_raw}</code>\n"
                    f"TX: <code>{sig}</code>",
                )
                actions.append({
                    "telegram_id": tid,
                    "action": "BUY",
                    "position_id": pid,
                    "mode": "LIVE",
                    "signature": sig,
                })
            except _live.SolanaLivePostExecutionError as exc:
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
                _live._notify(
                    app,
                    tid,
                    "🚨 <b>Solana LIVE BUY blocked</b>\n"
                    f"<code>{type(exc).__name__}: {str(exc)[:500]}</code>\n"
                    "This exact leader signal will not be retried automatically.",
                )
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc)})
        except Exception as exc:
            _live._notify(
                app,
                tid,
                "🚨 <b>Solana LIVE BUY preflight blocked</b>\n"
                f"<code>{type(exc).__name__}: {str(exc)[:500]}</code>",
            )
            actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc)})
    return actions


def monitor_positions_owner_changeset_4(app):
    """Run all existing exits, then force a protected full-exit attempt at 33m.

    The current monitor already implements the configured +15% take-profit and
    30-minute profitable max-hold.  Any position still OPEN at 33 minutes is sent
    through the existing protected 100% close path without a profit requirement.
    The close path may still refuse/deflect an unsafe transaction or use its
    emergency liquidity protections; this patch never bypasses them.
    """
    result = _PREV_MONITOR(app)
    now = int(time.time())
    try:
        with closing(_sol.connect(app)) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM positions WHERE status='OPEN' AND mode='LIVE' ORDER BY entry_ts"
            ).fetchall()]
    except Exception:
        return result

    for position in rows:
        tid = str(position.get("telegram_id") or "")
        if not tid or not _live.live_enabled(app, tid):
            continue
        age = max(0, now - _sol._int(position.get("entry_ts"), now))
        if age < OWNER_FORCE_EXIT_SECONDS:
            continue
        try:
            _live._close_live(
                app,
                tid,
                position,
                Decimal(1),
                "SOLANA_OWNER_CHANGESET4_33M_FULL_EXIT",
            )
        except _live.SolanaLivePostExecutionError as exc:
            cfg = _sol.settings(app)
            _live._record_execution_fault(app, tid, cfg, exc)
            _live._notify(
                app,
                tid,
                "🚨 <b>Solana 33-minute full-exit landed without valid economic output</b>\n"
                f"<code>{str(exc)[:450]}</code>",
            )
        except Exception as exc:
            _live._notify(
                app,
                tid,
                "⚠️ <b>Solana 33-minute full-exit deferred by protected execution</b>\n"
                f"<code>{type(exc).__name__}: {str(exc)[:450]}</code>",
            )
    return result


def install() -> None:
    if getattr(_sol, "_owner_changeset_4_installed", False):
        return

    # Trade size and LP classification.
    _live.live_limits = live_limits_owner_changeset_4
    _pool.evaluate_rugcheck = evaluate_rugcheck_lp_revalidation

    # Preserve PoolCheck as the outer LIVE entry gate, but replace its inner
    # canonical process with the 10-position-compatible equivalent above.
    _pool._eligible_live_users = eligible_live_users_owner_changeset_4
    _pool._PREV_PROCESS = process_leader_event_owner_changeset_4
    _live.process_leader_event = _pool.process_leader_event_with_pool_risk

    # Preserve the positive-edge/platform/mint gate as the final leader-event
    # wrapper and point its inner process at the newly composed PoolCheck path.
    _edge._PREV_PROCESS = _live.process_leader_event
    _sol.process_leader_event = _edge.process_leader_event_positive_edge

    # All existing monitor safety (including LIQUIDITY_STUCK handling) runs first.
    _sol.monitor_positions = monitor_positions_owner_changeset_4

    _sol._owner_changeset_4_installed = True
    print(
        "[owner-changeset-4] approved=2026-08-29T10:38:58Z "
        "trade=0.005SOL max_positions=10 tp=15% normal_hold=30m force_full_exit=33m "
        "routine_partial_sells=false lp_unlocked=revalidate execution_safety=preserved"
    )


install()
