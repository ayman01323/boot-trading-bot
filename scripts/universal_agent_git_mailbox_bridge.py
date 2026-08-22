from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path
from typing import Any

from scripts.claude_git_mailbox_bridge import (
    MAILBOX_BRANCH,
    _MESSAGE_ID_RE,
    _content_api,
    _github_json,
    _headers,
    _token_from_env,
)

AGENTS = ("gpt", "claude", "gemini", "deepseek", "copilot")
_AGENT_SET = set(AGENTS)
INCOMING_PATHS = {agent: f".github/ai-mailbox/bus-from-{agent}.md" for agent in AGENTS}
OUTGOING_PATHS = {agent: f".github/ai-mailbox/bus-to-{agent}.md" for agent in AGENTS}
_ALLOWED_PATHS = set(INCOMING_PATHS.values()) | set(OUTGOING_PATHS.values())


def _validate_sender(sender: str) -> str:
    value = str(sender or "").strip().lower()
    if value not in _AGENT_SET:
        raise ValueError("sender must be GPT, CLAUDE, GEMINI, DEEPSEEK or COPILOT")
    return value


def fetch_fixed_file(repo: str, path: str, *, token: str) -> tuple[str, str]:
    if path not in _ALLOWED_PATHS:
        raise ValueError("mailbox path is not allowed")
    url = _content_api(repo, path) + "?ref=" + MAILBOX_BRANCH
    body = _github_json(url, token=token)
    if not isinstance(body, dict):
        raise RuntimeError("GitHub content response was not an object")
    encoded = str(body.get("content") or "").replace("\n", "")
    try:
        text = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError("mailbox content could not be decoded") from exc
    return text, str(body.get("sha") or "").strip()


def _split_message(text: str) -> tuple[str, dict[str, str], str]:
    lines = str(text or "").splitlines()
    first = lines[0].strip() if lines else ""
    headers: dict[str, str] = {}
    body_start = len(lines)
    for idx, line in enumerate(lines[1:], start=1):
        if not line.strip():
            body_start = idx + 1
            break
        if ":" not in line:
            raise ValueError("invalid AI_BUS header line")
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return first, headers, "\n".join(lines[body_start:]).strip()


def normalize_sender_message(text: str, sender: str) -> tuple[str, str, str]:
    sender = _validate_sender(sender)
    first, headers, body = _split_message(text)
    if first != "AI_BUS":
        raise ValueError("message must start with AI_BUS")

    message_id = headers.get("message_id", "")
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("invalid message_id")

    declared_sender = headers.get("from", "").strip().lower()
    if declared_sender != sender:
        raise ValueError("AI_BUS from does not match sender mailbox")

    target = headers.get("to", "").strip().lower()
    if target not in _AGENT_SET | {"all"}:
        raise ValueError("invalid AI_BUS target")
    if target == sender:
        raise ValueError("sender cannot target itself")

    if headers.get("mode", "").strip().lower() != "direct":
        raise ValueError("universal git mailbox requires DIRECT mode")
    if headers.get("max_hops", "").strip() != "1":
        raise ValueError("universal git mailbox requires max_hops: 1")
    if not body:
        raise ValueError("message body cannot be empty")
    if len(body) > 8000:
        raise ValueError("message body exceeds 8000 characters")

    return message_id, target, str(text or "").strip() + "\n"


def reply_to_message_id(text: str, sender: str) -> str:
    sender = _validate_sender(sender)
    first, headers = _headers(text)
    if first != "AI_BUS_REPLY":
        return ""
    if headers.get("from", "").strip().lower() != "bus":
        return ""
    if headers.get("to", "").strip().lower() != sender:
        return ""
    message_id = headers.get("message_id", "")
    return message_id if _MESSAGE_ID_RE.fullmatch(message_id) else ""


def select_pending(repo: str, *, token: str, sender: str) -> tuple[bool, str, str, str]:
    sender = _validate_sender(sender)
    try:
        incoming, _ = fetch_fixed_file(repo, INCOMING_PATHS[sender], token=token)
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return False, "", "", ""
        raise
    message_id, target, envelope = normalize_sender_message(incoming, sender)
    try:
        outgoing, _ = fetch_fixed_file(repo, OUTGOING_PATHS[sender], token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        outgoing = ""
    return reply_to_message_id(outgoing, sender) != message_id, message_id, target, envelope


def validate_bus_reply(message_id: str, sender: str, bus_reply: str) -> str:
    sender = _validate_sender(sender)
    if not _MESSAGE_ID_RE.fullmatch(str(message_id or "")):
        raise ValueError("invalid message id")
    text = str(bus_reply or "").strip()
    first, headers = _headers(text)
    if first != "AI_BUS_REPLY":
        raise ValueError("reply must start with AI_BUS_REPLY")
    if headers.get("message_id") != message_id:
        raise ValueError("AI bus reply message_id mismatch")
    if headers.get("from", "").strip().lower() != "bus":
        raise ValueError("AI bus reply must come from BUS")
    if headers.get("to", "").strip().lower() != sender:
        raise ValueError("AI bus reply recipient mismatch")
    return text + "\n"


def publish_reply(repo: str, *, token: str, sender: str, message_id: str, bus_reply: str) -> None:
    sender = _validate_sender(sender)
    content = validate_bus_reply(message_id, sender, bus_reply)
    path = OUTGOING_PATHS[sender]
    existing_sha = ""
    try:
        _, existing_sha = fetch_fixed_file(repo, path, token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    payload: dict[str, Any] = {
        "message": f"AI bus reply to {sender} mailbox {message_id}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": MAILBOX_BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    _github_json(_content_api(repo, path), token=token, method="PUT", payload=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge fixed per-agent git mailboxes to the bounded AI bus.")
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select-live")
    select.add_argument("--repo", required=True)
    select.add_argument("--sender", required=True, choices=AGENTS)
    select.add_argument("--message-output", required=True)

    publish = sub.add_parser("publish-live")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--sender", required=True, choices=AGENTS)
    publish.add_argument("--message-id", required=True)
    publish.add_argument("--reply-file", required=True)

    args = parser.parse_args()
    token = _token_from_env()

    if args.command == "select-live":
        pending, message_id, target, envelope = select_pending(args.repo, token=token, sender=args.sender)
        if pending:
            Path(args.message_output).write_text(envelope, encoding="utf-8")
        print(f"pending={'true' if pending else 'false'}")
        print(f"message_id={message_id}")
        print(f"target={target}")
        return 0

    publish_reply(
        args.repo,
        token=token,
        sender=args.sender,
        message_id=args.message_id,
        bus_reply=Path(args.reply_file).read_text(encoding="utf-8", errors="replace"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
