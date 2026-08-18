from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from . import profit_control_loop_patch as _control
from . import transaction_audit_worker_patch as _worker
from .user_registry import all_users

_PREV_HOURLY_REVIEW = _worker.run_hourly_gpt_review


def _d(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _active_master_ids(app) -> list[str]:
    """Return every ACTIVE MASTER Telegram ID, without the worker's display cap."""
    out: list[str] = []
    for row in all_users(app.csv_dir, enabled_only=False):
        if str(row.get("role") or "").upper() != "MASTER":
            continue
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        tid = str(row.get("telegram_id") or "").strip()
        if tid and tid not in out:
            out.append(tid)
    if not out:
        for tid in getattr(app, "telegram_chat_ids", []) or []:
            value = str(tid).strip()
            if value and value not in out:
                out.append(value)
    return out


def _summary_text(result: dict) -> str:
    control = result.get("control_loop") or {}
    if control.get("error"):
        return "\n".join([
            "🚨 PROFIT CONTROL UPDATE",
            "Control-loop update failed.",
            str(control.get("error") or "unknown error")[:1200],
            "LIVE/ARMED state was not changed by this reporting layer.",
        ])

    hour = control.get("hour") or {}
    wins = int(hour.get("wins") or 0)
    losses = int(hour.get("losses") or 0)
    closed = int(hour.get("closed_trades") or 0)
    net = _d(hour.get("net_sol"))
    pf = _d(hour.get("profit_factor"))
    objective_pass = wins > losses and net > 0 and pf > Decimal("1.10")
    changed = bool(control.get("profile_changed"))
    previous = str(control.get("previous_profile") or "BASELINE")
    active = str(control.get("active_profile") or previous)
    generated = int(control.get("generated_at") or 0)
    when = (
        datetime.fromtimestamp(generated, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if generated > 0 else "current cycle"
    )

    if changed:
        policy_line = f"Entry policy UPDATED: {previous} → {active}"
    else:
        policy_line = f"Entry policy retained: {active}"

    lines = [
        "📦 PROFIT CONTROL UPDATE",
        f"Cycle: {when}",
        policy_line,
        f"Closed LIVE trades: {closed}",
        f"Wins / losses: {wins} / {losses}",
        f"Realised net: {net:+f} SOL",
        f"Profit factor: {pf:f}",
        f"Hourly objective: {'PASS ✅' if objective_pass else 'NOT YET ❌'}",
        f"Successful leaders remembered: {int(control.get('successful_leaders') or 0)}",
        f"Leaders cooling down: {int(control.get('blocked_leaders') or 0)}",
    ]
    if control.get("ranking_error"):
        lines.append("Leader ranking warning: " + str(control.get("ranking_error"))[:500])
    lines += [
        "Target: wins > losses + positive realised net P&L + PF > 1.10.",
        "LIVE/ARMED state, trade capital, reserve, signing, simulation and circuit breakers: unchanged.",
    ]
    return "\n".join(lines)


def send_control_update_to_all_masters(app, result: dict) -> list[dict]:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return [{"telegram_id": "", "error": "telegram_bot_token not configured"}]
    text = _summary_text(result)
    errors: list[dict] = []
    for tid in _active_master_ids(app):
        try:
            response = _worker.requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": str(tid),
                    "text": text[:3900],
                    "protect_content": True,
                    "disable_notification": False,
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {payload}")
        except Exception as exc:
            errors.append({"telegram_id": str(tid), "error": f"{type(exc).__name__}: {exc}"[:700]})
    return errors


def run_hourly_review_with_master_control_summary(app, zip_path):
    result = _PREV_HOURLY_REVIEW(app, zip_path)
    try:
        result["master_control_summary_errors"] = send_control_update_to_all_masters(app, result)
    except Exception as exc:
        result["master_control_summary_errors"] = [
            {"telegram_id": "", "error": f"{type(exc).__name__}: {exc}"[:700]}
        ]
    return result


def install():
    if getattr(_control, "_master_control_summary_installed", False):
        return
    _worker.run_hourly_gpt_review = run_hourly_review_with_master_control_summary
    _control._master_control_summary_installed = True
    print("[profit-control-master-summary] every_cycle=true all_active_masters=true notify=true")


install()
