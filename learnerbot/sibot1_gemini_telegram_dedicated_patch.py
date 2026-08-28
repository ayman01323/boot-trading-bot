from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from dotenv import dotenv_values

from . import sibot1_gemini_solana_control_patch as _gemini
from . import sibot1_solana_live_bridge_patch as _bridge
from . import telegram as _tg

# Dedicated Telegram transport for Gemini. The trading/control semantics stay in
# sibot1_gemini_solana_control_patch; this module only supplies a separate token,
# long-polling loop and reply transport. It never changes a trading/risk gate.

_RUNTIME_ENV = Path("/var/tmp/gemini_telegram_runtime.env")
_PREV_START = _bridge._start
_PREV_NOTIFY = _gemini._notify
_TLS = threading.local()
_STARTED = False
_START_LOCK = threading.Lock()


def _token() -> str:
    direct = os.environ.get("GEMINI_TELEGRAM_BOT_TOKEN", "").strip()
    if direct:
        return direct
    try:
        values = dotenv_values(_RUNTIME_ENV) or {}
        return str(values.get("GEMINI_TELEGRAM_BOT_TOKEN") or "").strip()
    except Exception:
        return ""


def _notify(app, tid, text: str) -> None:
    token = str(getattr(_TLS, "token", "") or "").strip()
    if token:
        try:
            _tg.send_message(
                token,
                str(tid),
                str(text),
                parse_mode="HTML",
                protect_content=True,
            )
            return
        except Exception as exc:
            print("[gemini-telegram] send", type(exc).__name__)
            return
    _PREV_NOTIFY(app, tid, text)


def _set_commands(token: str) -> None:
    commands = [
        {"command": "gemini_status", "description": "Gemini Solana readiness/status"},
        {"command": "gemini_arm_live", "description": "Arm Gemini LIVE with CONFIRM"},
        {"command": "gemini_auto", "description": "Gemini AUTO on/off"},
        {"command": "gemini_disarm", "description": "Disable Gemini LIVE controls"},
        {"command": "gemini_stop", "description": "Stop Gemini new entries"},
    ]
    try:
        _tg._json("setMyCommands", token, payload={"commands": commands}, timeout=15)
    except Exception as exc:
        print("[gemini-telegram] set-commands", type(exc).__name__)


def _help(token: str, chat_id) -> None:
    try:
        _tg.send_message(
            token,
            str(chat_id),
            "\n".join([
                "🤖 <b>GEMINI TRADING BOT</b>",
                "",
                "<code>/gemini_status</code>",
                "<code>/gemini_arm_live CONFIRM</code>",
                "<code>/gemini_auto on CONFIRM</code>",
                "<code>/gemini_auto off</code>",
                "<code>/gemini_disarm</code>",
                "<code>/gemini_stop</code>",
            ]),
            parse_mode="HTML",
            protect_content=True,
        )
    except Exception as exc:
        print("[gemini-telegram] help", type(exc).__name__)


def _poll(app, token: str) -> None:
    offset = None
    try:
        me = _tg.get_me(token)
        print(
            "[gemini-telegram] configured=true bot=@%s"
            % str((me or {}).get("username") or "unknown")
        )
    except Exception as exc:
        print("[gemini-telegram] token-validation", type(exc).__name__)
        return

    _set_commands(token)

    while True:
        try:
            updates = _tg.get_updates(token, limit=50, offset=offset, timeout=25)
            for update in updates:
                try:
                    update_id = int(update.get("update_id") or 0)
                    offset = max(int(offset or 0), update_id + 1)
                except Exception:
                    pass

                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = str(message.get("text") or "").strip()
                if chat_id is None or not text:
                    continue

                cmd = text.split()[0].lower().split("@", 1)[0]
                if cmd == "/start":
                    _help(token, chat_id)
                    continue

                previous = getattr(_TLS, "token", None)
                _TLS.token = token
                try:
                    handled = _gemini._command(app, chat_id, text)
                finally:
                    if previous is None:
                        try:
                            delattr(_TLS, "token")
                        except AttributeError:
                            pass
                    else:
                        _TLS.token = previous
                if not handled:
                    _help(token, chat_id)
        except Exception as exc:
            print("[gemini-telegram] poll", type(exc).__name__)
            time.sleep(3)


def _start_with_dedicated_telegram(app):
    global _STARTED
    result = _PREV_START(app)
    with _START_LOCK:
        if _STARTED:
            return result
        token = _token()
        if not token:
            print("[gemini-telegram] configured=false secret=GEMINI_TELEGRAM_BOT_TOKEN")
            _STARTED = True
            return result
        threading.Thread(
            target=_poll,
            args=(app, token),
            daemon=True,
            name="gemini-dedicated-telegram-poller",
        ).start()
        _STARTED = True
    return result


def install() -> None:
    _gemini._notify = _notify
    _bridge._start = _start_with_dedicated_telegram
    print("[gemini-telegram] installed=true dedicated_token=true")


install()
