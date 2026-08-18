from __future__ import annotations

from . import transaction_audit_worker_patch as _worker


def send_gpt_review_message_live_wording(app, chat_id: str, result: dict) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return
    if not result.get("ok"):
        text = (
            "⚠️ Hourly GPT audit review failed\n"
            f"{str(result.get('error') or 'unknown error')[:900]}\n"
            "Transaction audit was still saved. LIVE/ARMED trading state was not changed."
        )
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
            "GPT candidate: TEST ONLY — it does not alter LIVE settings.",
            "Any change to real-money strategy still requires explicit approval.",
        ]
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
    print("[hourly-gpt-wording] LIVE engine unchanged; GPT candidate labelled TEST ONLY")


install()
