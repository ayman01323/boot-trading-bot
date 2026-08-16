from __future__ import annotations

from pathlib import Path

from .telegram import send_to_chats
from .user_registry import all_users

TEST_ALERT_VERSION = "hi-simo-test-v3"
MIN_TARGET_TEST_RECIPIENTS = 3


def challenge_chat_ids(app) -> list[str]:
    """Return every configured BOOT Telegram recipient, de-duplicated."""
    ids: list[str] = []
    for value in (app.telegram_chat_ids or []):
        value = str(value).strip()
        if value:
            ids.append(value)
    try:
        for row in all_users(app.csv_dir, enabled_only=True):
            if str(row.get("status") or "").upper() != "ACTIVE":
                continue
            value = str(row.get("telegram_id") or "").strip()
            if value:
                ids.append(value)
    except Exception:
        pass
    return list(dict.fromkeys(ids))


def send_target_test_once(app) -> dict:
    """Send a one-shot Hi Simo Telegram delivery test to all BOOT recipients."""
    marker = Path(app.data_dir) / f".{TEST_ALERT_VERSION}.sent"
    if marker.exists():
        return {"status": "ALREADY_SENT", "sent_chats": 0, "failed_chats": 0}

    recipients = challenge_chat_ids(app)
    if not recipients:
        return {"status": "NO_RECIPIENTS", "sent_chats": 0, "failed_chats": 0}

    if len(recipients) < MIN_TARGET_TEST_RECIPIENTS:
        warning = (
            "⚠️ BOOT TELEGRAM TEST NOT COMPLETED\n"
            f"Configured/active Telegram recipients found: {len(recipients)}\n"
            f"Required for this installation: {MIN_TARGET_TEST_RECIPIENTS}\n"
            "Hi Simo will be sent when all three recipients are available."
        )
        result = send_to_chats(app.telegram_bot_token, recipients, warning)
        return {
            "status": "NEED_THREE_RECIPIENTS",
            "recipient_count": len(recipients),
            "sent_chats": int(result.get("sent_chats") or 0),
            "failed_chats": int(result.get("failed_chats") or 0),
        }

    result = send_to_chats(app.telegram_bot_token, recipients, "Hi Simo")
    sent = int(result.get("sent_chats") or 0)
    failed = int(result.get("failed_chats") or 0)

    if sent == len(recipients) and failed == 0:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"version={TEST_ALERT_VERSION}\nrecipients={len(recipients)}\nsent={sent}\n",
            encoding="utf-8",
        )
        status = "SENT_ALL"
    else:
        status = "PARTIAL_FAILURE"
    return {
        "status": status,
        "recipient_count": len(recipients),
        "sent_chats": sent,
        "failed_chats": failed,
    }
