from __future__ import annotations

import time
from pathlib import Path

from . import cli as _cli
from .telegram import send_message

TARGET_CHAT_ID = "461513364"
MESSAGE = "Hi Keefek"
MARKER = ".telegram_hi_keefek_461513364_20260818_v1"
_PREV_APP = _cli._app


def _app_with_hi_keefek():
    app = _PREV_APP()
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return app

    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        print("[telegram-hi-keefek] skipped: TELEGRAM_BOT_TOKEN is not configured")
        return app

    try:
        messages = send_message(token, TARGET_CHAT_ID, MESSAGE)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"sent_epoch={int(time.time())}\nchat_id={TARGET_CHAT_ID}\nmessage={MESSAGE}\nmessages={messages}\n",
            encoding="utf-8",
        )
        print(
            f"[telegram-hi-keefek] sent chat_id={TARGET_CHAT_ID} "
            f"messages={messages} one_shot=true"
        )
    except Exception as exc:
        print(f"[telegram-hi-keefek] ERROR {type(exc).__name__}: {exc}")

    return app


_cli._app = _app_with_hi_keefek
