from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from .control import load_state, save_state


API_ROOT = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramSettings:
    token: str
    chat_ids: frozenset[str]
    poll_timeout_seconds: int = 25

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        # Deliberately do NOT fall back to TELEGRAM_BOT_TOKEN.  Grok must use a
        # dedicated token so it can never compete with SiRisky/Claude polling.
        token = str(os.environ.get("GROK_TELEGRAM_BOT_TOKEN") or "").strip()
        raw_ids = str(os.environ.get("GROK_TELEGRAM_CHAT_IDS") or "").strip()
        chat_ids = frozenset(x.strip() for x in raw_ids.replace(";", ",").split(",") if x.strip())
        try:
            timeout = max(5, min(50, int(os.environ.get("GROK_TELEGRAM_POLL_TIMEOUT", "25"))))
        except ValueError:
            timeout = 25
        if not token:
            raise SystemExit("GROK_TELEGRAM_BOT_TOKEN is required for the dedicated Grok receiver")
        if not chat_ids:
            raise SystemExit("GROK_TELEGRAM_CHAT_IDS is required for the dedicated Grok receiver")
        return cls(token=token, chat_ids=chat_ids, poll_timeout_seconds=timeout)


class TelegramApiError(RuntimeError):
    def __init__(self, status: int | None, method: str):
        super().__init__(f"Telegram API request failed: method={method} status={status or 'unknown'}")
        self.status = status
        self.method = method


def _api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        f"{API_ROOT}/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=max(35, int(payload.get("timeout") or 0) + 10)) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        # Never include exc/URL in logs because the Telegram token is embedded in it.
        raise TelegramApiError(getattr(exc, "code", None), method) from None
    except Exception:
        raise TelegramApiError(None, method) from None
    if not isinstance(data, dict) or not data.get("ok"):
        raise TelegramApiError(None, method)
    return data


def _send_message(settings: TelegramSettings, chat_id: str, text: str) -> None:
    _api_call(
        settings.token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        },
    )


def _status_text() -> str:
    state = load_state()
    return "\n".join(
        [
            "🤖 GROK KNOWN-ASSETS BOT",
            f"PAPER arm: {'🟢 ARMED' if state.get('armed') else '⚪ OFF'}",
            "Market feed: REAL PUBLIC DATA",
            "Execution: PAPER ONLY",
            "Real-money signing: DISABLED",
            "Transaction broadcast: DISABLED",
        ]
    )


def _normalise_command(text: str) -> tuple[str, list[str]]:
    parts = str(text or "").strip().split()
    if not parts:
        return "", []
    command = parts[0].lower().split("@", 1)[0]
    return command, parts[1:]


def handle_command(text: str, chat_id: str) -> str | None:
    command, args = _normalise_command(text)
    if command in {"/start", "/help"}:
        return (
            "Grok PAPER controls:\n"
            "/grokstatus\n"
            "/grokarm on CONFIRM\n"
            "/grokarm off\n"
            "/grokstop\n\n"
            "These commands control Grok only. LIVE money execution is not exposed."
        )
    if command == "/grokstatus":
        return _status_text()
    if command == "/grokstop":
        save_state(armed=False, updated_by=f"telegram:{chat_id}")
        return "🛑 GROK PAPER DISARMED. New PAPER entries are blocked."
    if command == "/grokarm":
        if len(args) == 1 and args[0].lower() == "off":
            save_state(armed=False, updated_by=f"telegram:{chat_id}")
            return "✅ GROK PAPER ARM: OFF."
        if len(args) == 2 and args[0].lower() == "on" and args[1].upper() == "CONFIRM":
            save_state(armed=True, updated_by=f"telegram:{chat_id}")
            return (
                "✅ GROK PAPER ARMED. Grok may open PAPER positions when research and risk gates pass.\n"
                "🔒 Real-money signing and transaction broadcast remain disabled."
            )
        return "❌ Use exactly: /grokarm on CONFIRM  or  /grokarm off"
    # Dedicated receiver ignores all non-Grok commands rather than impersonating
    # another bot or producing a catch-all response.
    return None


def _iter_updates(settings: TelegramSettings, offset: int) -> tuple[list[dict[str, Any]], int]:
    data = _api_call(
        settings.token,
        "getUpdates",
        {
            "offset": offset,
            "timeout": settings.poll_timeout_seconds,
            "allowed_updates": ["message"],
        },
    )
    result = data.get("result") or []
    if not isinstance(result, list):
        return [], offset
    next_offset = offset
    updates: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        update_id = item.get("update_id")
        if isinstance(update_id, int):
            next_offset = max(next_offset, update_id + 1)
        updates.append(item)
    return updates, next_offset


def run() -> int:
    settings = TelegramSettings.from_env()
    stopping = False

    def _stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Validate that this token belongs to an accessible Telegram bot without
    # printing any token-bearing URL or response payload.
    _api_call(settings.token, "getMe", {})
    print(
        json.dumps(
            {
                "status": "STARTED",
                "receiver": "GROK_DEDICATED_TELEGRAM",
                "paper_only": True,
                "authorised_chat_count": len(settings.chat_ids),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )

    offset = 0
    consecutive_errors = 0
    while not stopping:
        try:
            updates, offset = _iter_updates(settings, offset)
            consecutive_errors = 0
            for update in updates:
                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat")
                if not isinstance(chat, dict):
                    continue
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id or chat_id not in settings.chat_ids:
                    continue
                text = message.get("text")
                if not isinstance(text, str):
                    continue
                reply = handle_command(text, chat_id)
                if reply:
                    _send_message(settings, chat_id, reply)
        except TelegramApiError as exc:
            consecutive_errors += 1
            # Status 409 normally means another poller is using the same token.
            # With a dedicated token this is an operational misconfiguration,
            # never a reason to fall back to the shared SiRisky token.
            print(
                json.dumps(
                    {
                        "status": "TELEGRAM_ERROR",
                        "http_status": exc.status,
                        "method": exc.method,
                        "consecutive_errors": consecutive_errors,
                    },
                    separators=(",", ":"),
                ),
                file=sys.stderr,
                flush=True,
            )
            time.sleep(min(30.0, 2.0 * consecutive_errors))

    print('{"status":"STOPPED","receiver":"GROK_DEDICATED_TELEGRAM"}', flush=True)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
