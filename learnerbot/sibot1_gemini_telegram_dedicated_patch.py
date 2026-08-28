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


def _prepare_long_poll(token: str) -> None:
    """Guarantee that Telegram will permit getUpdates for this dedicated bot.

    A valid token can still be unable to long-poll when a webhook is configured.
    deleteWebhook is idempotent; keeping pending updates avoids silently losing a
    user's command while the transport changes modes.
    """
    try:
        before = _tg.get_webhook_info(token) or {}
        had_webhook = bool(str(before.get("url") or "").strip())
    except Exception as exc:
        print("[gemini-telegram] webhook-info", type(exc).__name__)
        had_webhook = False

    _tg._json(
        "deleteWebhook",
        token,
        payload={"drop_pending_updates": False},
        timeout=15,
    )

    after = _tg.get_webhook_info(token) or {}
    if str(after.get("url") or "").strip():
        raise RuntimeError("Gemini Telegram webhook remained configured")
    print(
        "[gemini-telegram] long-poll-mode=true webhook-cleared=%s pending-preserved=true"
        % ("true" if had_webhook else "false")
    )


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
        _prepare_long_poll(token)
    except Exception as exc:
        print("[gemini-telegram] startup", type(exc).__name__)
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


def _wait_for_token_and_poll(app) -> None:
    # Deployment and secret-sync workflows can run concurrently. Keep waiting
    # rather than permanently disabling Gemini if the service starts first. If
    # Telegram startup itself fails, retry rather than leaving a dead daemon.
    last_notice = 0.0
    while True:
        token = _token()
        if token:
            _poll(app, token)
            print("[gemini-telegram] restarting-after-startup-failure=true")
            time.sleep(3)
            continue
        now = time.time()
        if now - last_notice >= 30:
            print("[gemini-telegram] waiting-for-secret=GEMINI_TELEGRAM_BOT_TOKEN")
            last_notice = now
        time.sleep(3)


def _start_with_dedicated_telegram(app):
    global _STARTED
    result = _PREV_START(app)
    with _START_LOCK:
        if _STARTED:
            return result
        threading.Thread(
            target=_wait_for_token_and_poll,
            args=(app,),
            daemon=True,
            name="gemini-dedicated-telegram-bootstrap",
        ).start()
        _STARTED = True
    return result


def install() -> None:
    _gemini._notify = _notify
    _bridge._start = _start_with_dedicated_telegram
    print("[gemini-telegram] installed=true dedicated_token=true resilient_secret_wait=true")


install()
