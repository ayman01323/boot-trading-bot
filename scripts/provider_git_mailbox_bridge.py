from __future__ import annotations

import argparse
import base64
import urllib.parse
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

ALLOWED_PROVIDERS = ("deepseek", "gemini", "grok", "copilot")


def _provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported mailbox provider")
    return provider


def request_path(provider: str) -> str:
    provider = _provider(provider)
    return f".github/ai-mailbox/gpt-to-{provider}.md"


def response_path(provider: str) -> str:
    provider = _provider(provider)
    return f".github/ai-mailbox/{provider}-to-gpt.md"


def fetch_provider_file(repo: str, provider: str, *, response: bool, token: str) -> tuple[str, str]:
    path = response_path(provider) if response else request_path(provider)
    url = _content_api(repo, path) + "?ref=" + urllib.parse.quote(MAILBOX_BRANCH, safe="")
    body = _github_json(url, token=token)
    if not isinstance(body, dict):
        raise RuntimeError("GitHub content response was not an object")
    encoded = str(body.get("content") or "").replace("\n", "")
    try:
        text = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError("mailbox content could not be decoded") from exc
    return text, str(body.get("sha") or "").strip()


def request_id_from_gpt(provider: str, text: str) -> str:
    provider = _provider(provider)
    first, headers = _headers(text)
    if first != f"GPT_TO_{provider.upper()}" or headers.get("status", "").upper() != "REQUEST":
        return ""
    message_id = headers.get("message_id", "")
    return message_id if _MESSAGE_ID_RE.fullmatch(message_id) else ""


def reply_to_request_id(provider: str, text: str) -> str:
    provider = _provider(provider)
    first, headers = _headers(text)
    if first != f"{provider.upper()}_TO_GPT":
        return ""
    message_id = headers.get("in_reply_to", "")
    return message_id if _MESSAGE_ID_RE.fullmatch(message_id) else ""


def select_pending(repo: str, provider: str, *, token: str) -> tuple[bool, str, str]:
    provider = _provider(provider)
    incoming, _ = fetch_provider_file(repo, provider, response=False, token=token)
    message_id = request_id_from_gpt(provider, incoming)
    if not message_id:
        return False, "", ""
    try:
        outgoing, _ = fetch_provider_file(repo, provider, response=True, token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        outgoing = ""
    pending = reply_to_request_id(provider, outgoing) != message_id
    return pending, message_id, incoming


def validate_provider_reply(provider: str, request_id: str, text: str) -> None:
    provider = _provider(provider)
    if not _MESSAGE_ID_RE.fullmatch(str(request_id or "")):
        raise ValueError("invalid request id")
    first, headers = _headers(text)
    if first != f"{provider.upper()}_TO_GPT":
        raise ValueError("provider reply has invalid prefix")
    if headers.get("in_reply_to", "") != request_id:
        raise ValueError("provider reply does not match request id")


def publish_reply(repo: str, provider: str, *, token: str, request_id: str, reply: str) -> None:
    provider = _provider(provider)
    validate_provider_reply(provider, request_id, reply)
    existing_sha = ""
    try:
        _, existing_sha = fetch_provider_file(repo, provider, response=True, token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    path = response_path(provider)
    payload: dict[str, Any] = {
        "message": f"{provider.title()} to GPT Strategy Factory {request_id}",
        "content": base64.b64encode(str(reply).encode("utf-8")).decode("ascii"),
        "branch": MAILBOX_BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    _github_json(_content_api(repo, path), token=token, method="PUT", payload=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage event-driven GPT/provider Strategy Factory fallback state.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("select-live")
    p.add_argument("--repo", required=True)
    p.add_argument("--provider", required=True, choices=ALLOWED_PROVIDERS)
    p.add_argument("--message-output", required=True)

    p = sub.add_parser("publish-live")
    p.add_argument("--repo", required=True)
    p.add_argument("--provider", required=True, choices=ALLOWED_PROVIDERS)
    p.add_argument("--request-id", required=True)
    p.add_argument("--reply-file", required=True)

    args = parser.parse_args()
    token = _token_from_env()
    if args.command == "select-live":
        pending, message_id, incoming = select_pending(args.repo, args.provider, token=token)
        if pending:
            Path(args.message_output).write_text(incoming, encoding="utf-8")
        print("pending=" + ("true" if pending else "false"))
        print("message_id=" + message_id)
        return 0
    if args.command == "publish-live":
        reply = Path(args.reply_file).read_text(encoding="utf-8", errors="replace")
        publish_reply(args.repo, args.provider, token=token, request_id=args.request_id, reply=reply)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
