from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol
from . import solana_live_patch as _live
from . import solana_profit_guard_patch as _guard
from . import solana_partial_sell_profit_guard_patch as _partial_guard

VMV_MINT = "CABUWcNoMGNgwnjssLzRWjuRcRDyz5N9NagyRiG9pump"
_HARD_PLATFORM_TARGET_PF = Decimal("1.30")
_HARD_PLATFORM_MIN_CLOSED = 5
_HARD_PLATFORM_COOLDOWN_MINUTES = 360
_HARD_FIRST_LEADER_LOSS_QUARANTINE_MINUTES = 1440
_HARD_MINT_FIRST_LOSS_QUARANTINE_MINUTES = 1440
_HARD_MINT_REPEAT_LOSS_QUARANTINE_MINUTES = 4320
_HARD_MINT_REPEAT_LOSS_COUNT = 2
_HARD_MINT_RECOVERY_PF = Decimal("1.30")

# Final BUY-side profitability gate. A leader can have a positive PF yet still
# have returns too small for the follower after fixed Solana costs.  The platform
# also now requires actual LIVE realised profit amount to beat realised loss
# amount by a safety margin before normal-frequency BUYs resume.
_sol.DEFAULTS.update({
    "live_min_leader_median_return_pct": (
        "5.0",
        "Minimum median reconstructed leader return percent before a LIVE copied BUY",
    ),
    "live_min_leader_recent_median_return_pct": (
        "4.0",
        "Minimum median return percent across the leader's recent closed trades before LIVE copy",
    ),
    "live_edge_recent_trade_window": (
        "10",
        "Recent reconstructed leader trades used by the positive-edge entry gate",
    ),
    "live_quarantine_after_first_copied_loss_minutes": (
        "1440",
        "Quarantine a leader after the first realised losing LIVE copy",
    ),
    "live_vmv_allowlist_enabled": (
        "true",
        "Allow VMV BUY signals to bypass only the leader median-return edge gate; realised amount and mint-loss gates still apply",
    ),
    "live_vmv_mint": (
        VMV_MINT,
        "Operator-approved VMV Solana mint",
    ),
    "live_platform_amount_window_trades": (
        "20",
        "Recent closed LIVE positions used by the platform realised profit-versus-loss amount gate",
    ),
    "live_platform_amount_min_closed": (
        "5",
        "Minimum closed LIVE positions before the platform amount gate becomes binding",
    ),
    "live_platform_target_profit_factor": (
        "1.30",
        "Required realised gross-profit/gross-loss amount ratio before normal LIVE BUY frequency",
    ),
    "live_platform_loss_cooldown_minutes": (
        "360",
        "Cooldown after a losing realised platform window before one recovery canary may be attempted",
    ),
    "live_platform_recovery_canary": (
        "true",
        "After cooldown allow only one LIVE recovery BUY until that position closes and amount performance is re-evaluated",
    ),
    "live_mint_loss_lookback_days": (
        "7",
        "Lookback for actual LIVE mint-level realised loss quarantine",
    ),
    "live_mint_first_loss_quarantine_minutes": (
        "1440",
        "Quarantine a mint for 24 hours after its latest actual LIVE close is a loss",
    ),
    "live_mint_repeat_loss_quarantine_minutes": (
        "4320",
        "Quarantine a repeatedly losing mint for 72 hours",
    ),
    "live_mint_repeat_loss_count": (
        "2",
        "Recent actual LIVE mint losses that trigger the longer quarantine when mint PF remains poor",
    ),
    "live_mint_recovery_profit_factor": (
        "1.30",
        "Mint-level realised profit factor needed to avoid the repeated-loss quarantine",
    ),
})

_PREV_PROCESS = _sol.process_leader_event
_PREV_COPIED_OK = _guard._copied_ok


def _d(v, default="0") -> Decimal:
    return _sol._dec(v, default)


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / Decimal(2)


def _pf(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def leader_return_edge(app, wallet: str, cfg: dict) -> dict:
    lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    recent_n = max(3, min(50, _sol._int(cfg.get("live_edge_recent_trade_window"), 10)))
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
            (str(wallet), cutoff),
        ).fetchall()]

    returns = []
    for row in rows:
        cost = _d(row.get("cost_sol"), 0)
        net = _d(row.get("net_sol"), 0)
        if cost <= 0:
            continue
        pct = net * Decimal(100) / cost
        returns.append(max(Decimal("-95"), min(Decimal("500"), pct)))

    recent = returns[-recent_n:]
    positive = [x for x in returns if x > 0]
    return {
        "closed": len(returns),
        "median_return_pct": _median(returns),
        "recent_closed": len(recent),
        "recent_median_return_pct": _median(recent),
        "median_positive_return_pct": _median(positive),
    }


def _edge_ok(app, wallet: str, cfg: dict) -> tuple[bool, str, dict]:
    metrics = leader_return_edge(app, wallet, cfg)
    min_closed = max(3, _sol._int(cfg.get("min_closed_trades"), 5))
    if int(metrics["closed"]) < min_closed:
        return False, f"leader has only {metrics['closed']} return samples; need {min_closed}", metrics

    historical_floor = max(Decimal(0), _d(cfg.get("live_min_leader_median_return_pct"), "5"))
    recent_floor = max(Decimal(0), _d(cfg.get("live_min_leader_recent_median_return_pct"), "4"))
    historical = _d(metrics.get("median_return_pct"), 0)
    recent = _d(metrics.get("recent_median_return_pct"), 0)

    if historical < historical_floor:
        return False, f"leader median return {historical:.3f}% is below LIVE edge floor {historical_floor:.3f}%", metrics
    if recent < recent_floor:
        return False, f"leader recent median return {recent:.3f}% is below LIVE edge floor {recent_floor:.3f}%", metrics
    return True, "ok", metrics


def _latest_copied_result(app, tid: str, wallet: str):
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                """SELECT realised_net_sol,closed_at FROM positions
                   WHERE telegram_id=? AND leader_wallet=? AND status='CLOSED' AND mode='LIVE'
                   ORDER BY closed_at DESC LIMIT 1""",
                (str(tid), str(wallet)),
            ).fetchone()
        if not row:
            return None
        return _d(row["realised_net_sol"], 0), int(row["closed_at"] or 0)
    except Exception:
        return None


def copied_ok_quarantine_first_loss(app, tid, wallet, cfg):
    latest = _latest_copied_result(app, tid, wallet)
    if latest:
        net, closed_at = latest
        cooldown_minutes = max(
            _HARD_FIRST_LEADER_LOSS_QUARANTINE_MINUTES,
            max(5, _sol._int(cfg.get("live_quarantine_after_first_copied_loss_minutes"), 1440)),
        )
        if net < 0 and int(time.time()) < int(closed_at) + cooldown_minutes * 60:
            return False
    return _PREV_COPIED_OK(app, tid, wallet, cfg)


def _amount_stats(rows: list[dict]) -> dict:
    vals = [_d(r.get("realised_net_sol"), 0) for r in rows]
    profit = sum((v for v in vals if v > 0), Decimal(0))
    loss = sum((-v for v in vals if v < 0), Decimal(0))
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    return {
        "closed": len(vals),
        "wins": wins,
        "losses": losses,
        "gross_profit_sol": profit,
        "gross_loss_sol": loss,
        "net_sol": profit - loss,
        "profit_factor": _pf(profit, loss),
        "latest_closed_at": max((int(r.get("closed_at") or 0) for r in rows), default=0),
    }


def platform_live_amount_metrics(app, cfg: dict) -> dict:
    window = max(5, min(100, _sol._int(cfg.get("live_platform_amount_window_trades"), 20)))
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT realised_net_sol,closed_at FROM positions
               WHERE status='CLOSED' AND mode='LIVE'
               ORDER BY closed_at DESC LIMIT ?""",
            (window,),
        ).fetchall()]
    return _amount_stats(rows)


def mint_live_amount_metrics(app, mint: str, cfg: dict) -> dict:
    days = max(1, min(30, _sol._int(cfg.get("live_mint_loss_lookback_days"), 7)))
    cutoff = int(time.time()) - days * 86400
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT realised_net_sol,closed_at FROM positions
               WHERE mint=? AND status='CLOSED' AND mode='LIVE' AND COALESCE(closed_at,0)>=?
               ORDER BY closed_at DESC LIMIT 50""",
            (str(mint), cutoff),
        ).fetchall()]
    metrics = _amount_stats(rows)
    metrics["latest_net_sol"] = _d(rows[0].get("realised_net_sol"), 0) if rows else Decimal(0)
    return metrics


def _mint_loss_gate(app, mint: str, cfg: dict) -> tuple[bool, str, dict]:
    try:
        metrics = mint_live_amount_metrics(app, mint, cfg)
    except Exception as exc:
        return False, f"mint realised-loss history unavailable: {type(exc).__name__}: {str(exc)[:180]}", {}
    if int(metrics.get("closed") or 0) <= 0 or _d(metrics.get("latest_net_sol"), 0) >= 0:
        return True, "ok", metrics

    repeat_count = max(
        _HARD_MINT_REPEAT_LOSS_COUNT,
        max(1, _sol._int(cfg.get("live_mint_repeat_loss_count"), 2)),
    )
    recovery_pf = max(
        _HARD_MINT_RECOVERY_PF,
        max(Decimal(1), _d(cfg.get("live_mint_recovery_profit_factor"), "1.30")),
    )
    first_minutes = max(
        _HARD_MINT_FIRST_LOSS_QUARANTINE_MINUTES,
        max(5, _sol._int(cfg.get("live_mint_first_loss_quarantine_minutes"), 1440)),
    )
    repeat_minutes = max(
        _HARD_MINT_REPEAT_LOSS_QUARANTINE_MINUTES,
        max(first_minutes, _sol._int(cfg.get("live_mint_repeat_loss_quarantine_minutes"), 4320)),
    )
    repeated_bad = int(metrics.get("losses") or 0) >= repeat_count and _d(metrics.get("profit_factor"), 0) < recovery_pf
    cooldown = repeat_minutes if repeated_bad else first_minutes
    remaining = int(metrics.get("latest_closed_at") or 0) + cooldown * 60 - int(time.time())
    if remaining > 0:
        return False, (
            f"mint actual LIVE result is loss-making; quarantined for {max(1, (remaining + 59)//60)} more minutes "
            f"(losses={metrics.get('losses')}, PF={_d(metrics.get('profit_factor'), 0):.3f})"
        ), metrics
    return True, "ok", metrics


def _recovery_state(app) -> dict:
    try:
        with closing(_sol.connect(app)) as conn:
            raw = _sol._state(conn, "live_platform_amount_recovery", "") or ""
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _mark_recovery_canary(app, metrics: dict) -> None:
    try:
        state = {
            "baseline_closed_at": int(metrics.get("latest_closed_at") or 0),
            "baseline_closed": int(metrics.get("closed") or 0),
            "executed_at": int(time.time()),
        }
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _sol._set_state(conn, "live_platform_amount_recovery", json.dumps(state, separators=(",", ":")))
    except Exception:
        pass


def _platform_amount_gate(app, cfg: dict) -> tuple[bool, str, dict, bool]:
    try:
        metrics = platform_live_amount_metrics(app, cfg)
    except Exception as exc:
        return False, f"platform realised amount history unavailable: {type(exc).__name__}: {str(exc)[:180]}", {}, False

    min_closed = max(
        _HARD_PLATFORM_MIN_CLOSED,
        max(1, _sol._int(cfg.get("live_platform_amount_min_closed"), 5)),
    )
    if int(metrics.get("closed") or 0) < min_closed:
        return True, "insufficient LIVE sample; normal safety stack remains mandatory", metrics, False

    target_pf = max(
        _HARD_PLATFORM_TARGET_PF,
        max(Decimal(1), _d(cfg.get("live_platform_target_profit_factor"), "1.30")),
    )
    profit = _d(metrics.get("gross_profit_sol"), 0)
    loss = _d(metrics.get("gross_loss_sol"), 0)
    net = _d(metrics.get("net_sol"), 0)
    pf = _d(metrics.get("profit_factor"), 0)
    if net > 0 and profit > loss * target_pf and pf >= target_pf:
        return True, "realised profit amount exceeds loss target", metrics, False

    cooldown_minutes = max(
        _HARD_PLATFORM_COOLDOWN_MINUTES,
        max(5, _sol._int(cfg.get("live_platform_loss_cooldown_minutes"), 360)),
    )
    remaining = int(metrics.get("latest_closed_at") or 0) + cooldown_minutes * 60 - int(time.time())
    if remaining > 0:
        return False, (
            f"platform realised profit amount {profit:.9f} SOL is below required {target_pf:.2f}x loss amount "
            f"{loss:.9f} SOL (PF={pf:.3f}); recovery cooldown {max(1, (remaining + 59)//60)} min"
        ), metrics, False

    if not _sol._bool(cfg.get("live_platform_recovery_canary"), True):
        return False, f"platform PF {pf:.3f} is below {target_pf:.2f}; recovery canary disabled", metrics, False

    state = _recovery_state(app)
    baseline = int(metrics.get("latest_closed_at") or 0)
    if int(state.get("baseline_closed_at") or 0) == baseline and int(state.get("executed_at") or 0) > 0:
        return False, "one recovery LIVE BUY already executed; waiting for that position to close before re-evaluation", metrics, False

    try:
        with closing(_sol.connect(app)) as conn:
            open_live = int(conn.execute("SELECT COUNT(*) n FROM positions WHERE status='OPEN' AND mode='LIVE'").fetchone()["n"])
    except Exception as exc:
        return False, f"cannot prove recovery canary exclusivity: {type(exc).__name__}: {str(exc)[:160]}", metrics, False
    if open_live > 0:
        return False, "platform amount gate is in recovery mode and another LIVE position is still open", metrics, False

    armed_users = 0
    try:
        for user in _live.all_users(app.csv_dir, enabled_only=True):
            tid = str(user.get("telegram_id") or "")
            if tid and _live.live_enabled(app, tid):
                armed_users += 1
    except Exception:
        armed_users = 2
    if armed_users > 1:
        return False, "recovery canary requires at most one Solana LIVE user armed", metrics, False

    return True, "RECOVERY_CANARY", metrics, True


def _vmv_edge_exception(cfg: dict, event: dict) -> bool:
    if not _sol._bool(cfg.get("live_vmv_allowlist_enabled"), True):
        return False
    configured_mint = str(cfg.get("live_vmv_mint") or VMV_MINT).strip()
    event_mint = str((event or {}).get("mint") or "").strip()
    return bool(configured_mint and event_mint == configured_mint)


def process_leader_event_positive_edge(app, event: dict):
    """Reject weak/losing LIVE BUYs while never blocking SELL risk controls."""
    if str((event or {}).get("action") or "").upper() != "BUY":
        return _PREV_PROCESS(app, event)

    cfg = _sol.settings(app)
    wallet = str((event or {}).get("leader_wallet") or "")
    mint = str((event or {}).get("mint") or "")

    # VMV bypasses only the historical median-return filter. It cannot bypass an
    # actual realised platform loss circuit or its own mint's actual loss record.
    if not _vmv_edge_exception(cfg, event):
        if not wallet:
            return [{"action": "REJECT", "reason": "leader wallet missing from BUY signal"}]
        ok, reason, metrics = _edge_ok(app, wallet, cfg)
        if not ok:
            return [{
                "action": "REJECT",
                "reason": "POSITIVE_EDGE_GATE: " + reason,
                "leader_wallet": wallet,
                "median_return_pct": str(metrics.get("median_return_pct")),
                "recent_median_return_pct": str(metrics.get("recent_median_return_pct")),
            }]

    ok, reason, mint_metrics = _mint_loss_gate(app, mint, cfg)
    if not ok:
        return [{
            "action": "REJECT",
            "reason": "MINT_REALIZED_LOSS_GATE: " + reason,
            "mint": mint,
            "mint_profit_factor": str(mint_metrics.get("profit_factor", "")),
            "mint_losses": int(mint_metrics.get("losses") or 0),
        }]

    ok, reason, platform_metrics, recovery = _platform_amount_gate(app, cfg)
    if not ok:
        return [{
            "action": "REJECT",
            "reason": "PLATFORM_AMOUNT_PROFIT_GATE: " + reason,
            "gross_profit_sol": str(platform_metrics.get("gross_profit_sol", "")),
            "gross_loss_sol": str(platform_metrics.get("gross_loss_sol", "")),
            "profit_factor": str(platform_metrics.get("profit_factor", "")),
        }]

    result = _PREV_PROCESS(app, event)
    if recovery and any(str((row or {}).get("action") or "").upper() == "BUY" for row in (result or [])):
        _mark_recovery_canary(app, platform_metrics)
    return result


def install():
    if getattr(_sol, "_positive_edge_entry_gate_installed", False):
        return
    _guard._copied_ok = copied_ok_quarantine_first_loss
    _sol.process_leader_event = process_leader_event_positive_edge
    _sol._positive_edge_entry_gate_installed = True
    print(
        "[solana-positive-edge-entry] historical_median>=5% recent_median>=4% "
        "platform_realised_pf>=1.30 recovery_canary=single mint_loss_quarantine=24-72h "
        "first_copied_loss_quarantine=24h vmv_bypasses_edge_only"
    )


install()
