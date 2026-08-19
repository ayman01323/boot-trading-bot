from __future__ import annotations

import time
from pathlib import Path

from . import cli as _cli
# Load the direct per-user /reports navigation command after the compact menu layer.
from . import telegram_reports_direct_command_patch  # noqa: F401
# Start the root-owned daily full-folder backup only for the production `run` command.
from . import daily_botbuc_backup_patch  # noqa: F401
# Publish non-sensitive backup status under /tmp for read-only VPS diagnostics.
from . import daily_botbuc_status_patch  # noqa: F401
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

# Separate one-shot follow-up requested on 2026-08-18. It uses its own marker,
# so the original greeting is not resent and future restarts do not repeat this.
HIIII_MESSAGE = "hiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii"
HIIII_MARKER = ".telegram_hiiii_461513364_20260818_v1"
_PREV_APP_HIIII = _cli._app


def _app_with_hiiii():
    app = _PREV_APP_HIIII()
    marker = Path(app.data_dir) / HIIII_MARKER
    if marker.exists():
        return app

    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        print("[telegram-hiiii] skipped: TELEGRAM_BOT_TOKEN is not configured")
        return app

    try:
        messages = send_message(token, TARGET_CHAT_ID, HIIII_MESSAGE)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"sent_epoch={int(time.time())}\nchat_id={TARGET_CHAT_ID}\nmessage={HIIII_MESSAGE}\nmessages={messages}\n",
            encoding="utf-8",
        )
        print(
            f"[telegram-hiiii] sent chat_id={TARGET_CHAT_ID} "
            f"messages={messages} one_shot=true"
        )
    except Exception as exc:
        print(f"[telegram-hiiii] ERROR {type(exc).__name__}: {exc}")

    return app


_cli._app = _app_with_hiiii
