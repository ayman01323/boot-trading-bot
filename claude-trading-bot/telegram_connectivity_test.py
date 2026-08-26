"""One-time Telegram connectivity/format proof for THIS isolated Claude
instance only -- its own token, its own master chat ids, its own DATA_DIR
marker. Never auto-installed into any patch chain and never called from
claude_bot_patches.install_all() -- the only way this ever fires is a human
operator explicitly running:

    python run.py send-test-telegram

Replaces a prior version of this idea (learnerbot/telegram_claude_smoke_patch.py,
removed 2026-08-26) that lived inside the shared production package and
auto-installed itself via learnerbot/final_runtime_integrity_patch.py's
unconditional import chain -- with no gate distinguishing the isolated
Claude instance from production, it would have fired identically (and sent
a real message through PRODUCTION's own bot token) on production's own next
restart. This version cannot do that: it is not imported by anything except
run.py's explicit `send-test-telegram` subcommand, and it only ever uses
THIS instance's own AppSettings (this instance's own CSV_DIR/DATA_DIR/
TELEGRAM_BOT_TOKEN, loaded the same isolated way run.py always loads them).
"""

from __future__ import annotations

from pathlib import Path

MARKER = ".claude_telegram_connectivity_test_v1"
MESSAGE = (
    "🤖 CLAUDE TRADING BOT\n\n"
    "Test message — connectivity check only.\n"
    "This confirms message delivery to this Telegram destination from the "
    "isolated Claude bot instance. No trade, balance, or wallet action is "
    "associated with this message.\n\n"
    "Mode: SHADOW (ARMED=false)\n"
    "Instance: claude-trading-bot (isolated from production)"
)


def already_sent(app) -> bool:
    return (Path(app.data_dir) / MARKER).exists()


def send_once(app) -> dict:
    """Sends MESSAGE to this instance's own configured master chat id(s),
    exactly once (marker-gated). Returns a small report dict; never raises
    on delivery failure -- a connectivity check must be able to report
    failure, not crash the caller."""
    from learnerbot.telegram import send_to_chats
    from learnerbot.user_registry import all_users

    if already_sent(app):
        return {"sent": False, "reason": "already sent (marker present)"}

    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return {"sent": False, "reason": "TELEGRAM_BOT_TOKEN not configured for this instance"}

    chat_ids = [
        str(row.get("telegram_id") or "").strip()
        for row in all_users(app.csv_dir)
        if str(row.get("telegram_id") or "").strip()
    ]
    if not chat_ids:
        return {"sent": False, "reason": "no registered chat ids in this instance's own user registry"}

    try:
        result = send_to_chats(token, chat_ids, MESSAGE, protect_content=True, disable_notification=False)
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}

    delivered = int((result or {}).get("sent_chats") or 0) if isinstance(result, dict) else int(result or 0)
    if delivered <= 0:
        return {"sent": False, "reason": "no successful delivery, marker not written"}

    marker = Path(app.data_dir) / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("sent\n", encoding="utf-8")
    return {"sent": True, "delivered_to": delivered}
