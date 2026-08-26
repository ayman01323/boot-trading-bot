from __future__ import annotations

"""One-time Claude-labelled Telegram connectivity proof via the existing router.

This intentionally reuses the already-running VPS Telegram process and its
existing credential. It never polls Telegram, never copies the bot token to the
Google server, and never changes any trading/LIVE/ARM/AUTO/signer state.
"""

from pathlib import Path

from . import cli as _cli
from . import telegram as _tg
from .ai_ops_status import master_chat_ids

_PREV_APP = _cli._app
_MARKER = ".claude_telegram_smoke_v1"
_MESSAGE = (
    "🤖 CLAUDE TRADING BOT — test message. "
    "STATUS=SIMULATED, LIVE=OFF, ARMED=OFF. "
    "This is a connectivity/format check only, no trade action taken."
)


def _send_once(app) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return
    marker = Path(app.data_dir) / _MARKER
    if marker.exists():
        return
    chats = [str(value).strip() for value in master_chat_ids(app.csv_dir) if str(value).strip()]
    if not chats:
        return
    try:
        result = _tg.send_to_chats(
            token,
            chats,
            _MESSAGE,
            protect_content=True,
            disable_notification=False,
        )
        delivered = int((result or {}).get("sent_chats") or 0) if isinstance(result, dict) else int(result or 0)
        if delivered <= 0:
            print("[claude-telegram-smoke] no successful MASTER delivery; marker not written")
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("sent\n", encoding="utf-8")
        print(f"[claude-telegram-smoke] sent=true master_chats={delivered}")
    except Exception as exc:
        # Connectivity diagnostics must never stop the trading service startup.
        print(f"[claude-telegram-smoke] sent=false error={type(exc).__name__}")


def _app_with_claude_smoke():
    app = _PREV_APP()
    _send_once(app)
    return app


def install() -> None:
    if getattr(_cli, "_telegram_claude_smoke_patch_installed", False):
        return
    _cli._app = _app_with_claude_smoke
    _cli._telegram_claude_smoke_patch_installed = True


install()
