from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

_ALLOWED_AGENTS = {"claude", "gemini", "deepseek", "copilot"}
_ALLOWED_KINDS = {"initiation", "reply"}
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_STATUS_RE = re.compile(r"^[A-Z_]{2,32}$")


def _headers(text: str) -> dict[str, str]:
    lines = str(text or "").splitlines()
    out: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip().lower()] = value.strip()
    return out


def status_from_file(path: str | None, fallback: str) -> str:
    status = str(fallback or "UNKNOWN").strip().upper()
    if path:
        headers = _headers(Path(path).read_text(encoding="utf-8", errors="replace"))
        status = str(headers.get("status") or status).strip().upper()
    return status if _STATUS_RE.fullmatch(status) else "UNKNOWN"


def build_message(agent: str, kind: str, message_id: str, status: str) -> str:
    agent = str(agent or "").strip().lower()
    kind = str(kind or "").strip().lower()
    message_id = str(message_id or "").strip()
    status = str(status or "UNKNOWN").strip().upper()
    if agent not in _ALLOWED_AGENTS:
        raise ValueError("unsupported agent")
    if kind not in _ALLOWED_KINDS:
        raise ValueError("unsupported notification kind")
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("invalid message id")
    if not _STATUS_RE.fullmatch(status):
        status = "UNKNOWN"

    icon = {"claude": "🟠", "gemini": "🟢", "deepseek": "🔴", "copilot": "🔵"}[agent]
    label = agent.upper()
    if kind == "initiation":
        headline = f"{icon} {label} → GPT MESSAGE"
        action = "New agent message received for GPT."
    else:
        headline = f"{icon} {label} → GPT REPLY"
        action = "Agent reply is ready for GPT."
    return (
        f"{headline}\n"
        f"Message ID: {message_id}\n"
        f"Status: {status}\n"
        f"{action}\n"
        f"Tell GPT: check {agent} Strategy Factory"
    )


def send_telegram(token: str, chat_id: str, text: str) -> None:
    token = str(token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    if not re.fullmatch(r"-?[0-9]{5,20}", chat_id):
        raise ValueError("TELEGRAM_MASTER_CHAT_ID is not configured")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": False,
            "protect_content": False,
            "link_preview_options": {"is_disabled": True},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "boot-ai-mailbox-notify"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram HTTP {int(exc.code or 500)}") from None
    except Exception as exc:
        raise RuntimeError(f"Telegram request failed: {type(exc).__name__}") from None
    if not bool(body.get("ok")):
        raise RuntimeError("Telegram API rejected notification")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a metadata-only Telegram alert for a bounded Strategy Factory fallback event.")
    parser.add_argument("--agent", required=True, choices=sorted(_ALLOWED_AGENTS))
    parser.add_argument("--kind", required=True, choices=sorted(_ALLOWED_KINDS))
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--status", default="UNKNOWN")
    parser.add_argument("--mailbox-file", default="")
    parser.add_argument("--skip-if-unconfigured", action="store_true")
    args = parser.parse_args()

    token = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(os.environ.get("TELEGRAM_MASTER_CHAT_ID") or "").strip()
    if args.skip_if_unconfigured and (not token or not chat_id):
        print("telegram_configured=false")
        return 0

    status = status_from_file(args.mailbox_file or None, args.status)
    text = build_message(args.agent, args.kind, args.message_id, status)
    send_telegram(token, chat_id, text)
    print("telegram_configured=true")
    print("telegram_sent=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
