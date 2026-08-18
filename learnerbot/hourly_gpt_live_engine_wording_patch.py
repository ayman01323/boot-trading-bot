from __future__ import annotations

from . import transaction_audit_worker_patch as _worker


def _control_lines(result: dict) -> list[str]:
    control = result.get("control_loop") or {}
    if not control:
        return []
    if control.get("error"):
        return ["", "📦 Profit Control Loop: ERROR", str(control.get("error"))[:500]]
    hour = control.get("hour") or {}
    changed = bool(control.get("profile_changed"))
    previous = str(control.get("previous_profile") or "")
    active = str(control.get("active_profile") or "BASELINE")
    lines = [
        "",
        "📦 PROFIT CONTROL LOOP",
        "Hour: %s wins / %s losses • net %s SOL • PF %s" % (
            hour.get("wins", 0), hour.get("losses", 0),
            hour.get("net_sol", "0"), hour.get("profit_factor", "0"),
        ),
        "Entry policy: %s%s" % (active, (" (changed from %s)" % previous) if changed else ""),
        "Successful leaders remembered: %s • cooling down: %s" % (
            control.get("successful_leaders", 0), control.get("blocked_leaders", 0),
        ),
        "Objective: wins > losses + positive realised net P&L + PF > 1.10.",
        "LIVE/ARMED state, capital, reserve, signing, simulation and circuit breakers: unchanged.",
    ]
    return lines


def send_gpt_review_message_live_wording(app, chat_id: str, result: dict) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return
    if not result.get("ok"):
        lines = [
            "⚠️ Hourly GPT audit review failed",
            str(result.get("error") or "unknown error")[:900],
            "Transaction audit was still saved. LIVE/ARMED trading state was not changed.",
        ]
        lines += _control_lines(result)
        text = "\n".join(lines)
    else:
        review = result.get("review") or {}
        findings = review.get("findings") or []
        lines = [
            "🧠 Hourly GPT trading-bot review",
            f"Status: {review.get('status', 'UNKNOWN')}",
            f"Action: {review.get('recommended_action', 'KEEP_CURRENT_LIVE_SETTINGS')}",
            str(review.get("executive_summary") or "")[:1200],
            "",
            "Top findings:",
        ]
        for finding in findings[:5]:
            lines.append(
                "• [%s/%s] %s" % (
                    finding.get("severity", ""),
                    finding.get("category", ""),
                    str(finding.get("interpretation") or finding.get("evidence") or "")[:450],
                )
            )
        lines += [
            "",
            "Trading engine: LIVE/ARMED state unchanged.",
            "GPT candidate: TEST ONLY; GPT does not directly write LIVE parameters.",
            "The deterministic Profit Control Loop may switch only among source-controlled bounded entry-quality profiles after measured LIVE evidence.",
        ]
        lines += _control_lines(result)
        text = "\n".join(lines)
    response = _worker.requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": str(chat_id),
            "text": text[:3900],
            "protect_content": True,
            "disable_notification": True,
            "link_preview_options": {"is_disabled": True},
        },
        timeout=30,
    )
    response.raise_for_status()


def install():
    _worker.send_gpt_review_message = send_gpt_review_message_live_wording
    print("[hourly-gpt-wording] LIVE engine unchanged; deterministic profit-control results displayed")


install()
