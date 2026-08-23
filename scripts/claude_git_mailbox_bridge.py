from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAILBOX_BRANCH = "ai-mailbox"
CLAUDE_TO_GPT_PATH = ".github/ai-mailbox/claude-to-gpt.md"
CLAUDE_API_TO_GPT_PATH = ".github/ai-mailbox/claude-api-to-gpt.md"
GPT_TO_CLAUDE_PATH = ".github/ai-mailbox/gpt-to-claude.md"
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_API_ROOT = "https://api.github.com"


def _token_from_env() -> str:
    return str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _github_json(url: str, *, token: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    if not token:
        raise RuntimeError("GitHub token is unavailable")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "boot-trading-bot-claude-git-mailbox-bridge",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {int(exc.code or 500)}: {raw[:600]}") from None
    except Exception as exc:
        raise RuntimeError(f"GitHub API request failed: {type(exc).__name__}") from None


def _validate_repo(repo: str) -> str:
    value = str(repo or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("invalid repository name")
    return value


def _content_api(repo: str, path: str) -> str:
    return f"{_API_ROOT}/repos/{_validate_repo(repo)}/contents/{urllib.parse.quote(path, safe='/')}"


def fetch_fixed_file(repo: str, path: str, *, token: str) -> tuple[str, str]:
    if path not in {CLAUDE_TO_GPT_PATH, CLAUDE_API_TO_GPT_PATH, GPT_TO_CLAUDE_PATH}:
        raise ValueError("mailbox path is not allowed")
    body = _github_json(_content_api(repo, path) + "?ref=" + urllib.parse.quote(MAILBOX_BRANCH, safe=""), token=token)
    if not isinstance(body, dict):
        raise RuntimeError("GitHub content response was not an object")
    encoded = str(body.get("content") or "").replace("\n", "")
    try:
        text = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError("mailbox content could not be decoded") from exc
    return text, str(body.get("sha") or "").strip()


def _headers(text: str) -> tuple[str, dict[str, str]]:
    lines = str(text or "").splitlines(); first = lines[0].strip() if lines else ""; values = {}
    for line in lines[1:]:
        if not line.strip(): break
        if ":" in line:
            key, value = line.split(":", 1); values[key.strip().lower()] = value.strip()
    return first, values


def message_id_from_claude(text: str) -> str:
    first, headers = _headers(text)
    if first != "CLAUDE_TO_GPT" or headers.get("identity", "").upper() == "STATELESS_API_RESPONDER":
        return ""
    message_id = headers.get("message_id", "")
    return message_id if _MESSAGE_ID_RE.fullmatch(message_id) else ""


def reply_to_message_id(text: str) -> str:
    first, headers = _headers(text)
    if first != "GPT_TO_CLAUDE": return ""
    message_id = headers.get("in_reply_to", "")
    return message_id if _MESSAGE_ID_RE.fullmatch(message_id) else ""


def normalize_claude_message(text: str) -> tuple[str, str]:
    message_id = message_id_from_claude(text)
    if not message_id: raise ValueError("invalid persistent CLAUDE_TO_GPT mailbox message")
    payload = str(text or "").strip()
    if len(payload) > 7400: payload = payload[:7400].rstrip() + "\n\n[Claude git mailbox message truncated by bounded bridge]"
    return message_id, (
        "AI_BUS\n" f"message_id: {message_id}\n" "from: CLAUDE\n" "to: GPT\n" "mode: DIRECT\n" "max_hops: 1\n\n"
        "This message came from the persistent/interactive Claude mailbox, not the stateless Anthropic API responder. "
        "Treat it as communication-only.\n\n" f"{payload}\n"
    )


def select_pending(repo: str, *, token: str) -> tuple[bool, str, str]:
    incoming, _ = fetch_fixed_file(repo, CLAUDE_TO_GPT_PATH, token=token)
    message_id, envelope = normalize_claude_message(incoming)
    try: outgoing, _ = fetch_fixed_file(repo, GPT_TO_CLAUDE_PATH, token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc): raise
        outgoing = ""
    return reply_to_message_id(outgoing) != message_id, message_id, envelope


def build_gpt_mailbox_reply(message_id: str, bus_reply: str) -> str:
    if not _MESSAGE_ID_RE.fullmatch(str(message_id or "")): raise ValueError("invalid message id")
    text = str(bus_reply or "").strip(); first, headers = _headers(text)
    if first != "AI_BUS_REPLY" or headers.get("message_id") != message_id: raise ValueError("AI bus reply does not match Claude mailbox message")
    status = headers.get("status", "BLOCKED").upper()
    if status not in {"COMPLETED", "PARTIAL", "BLOCKED"}: status = "BLOCKED"
    return (
        "GPT_TO_CLAUDE\n" f"in_reply_to: {message_id}\n" f"status: {status}\n" "identity: GPT_API_RESPONDER\n"
        "transport: AI_BUS_VIA_GIT_MAILBOX\n"
        "constraints: communication-only; no deploy; no trading/risk/capital/wallet/signing changes; no secrets\n\n" f"{text}\n"
    )


def publish_reply(repo: str, *, token: str, message_id: str, bus_reply: str) -> None:
    content = build_gpt_mailbox_reply(message_id, bus_reply); existing_sha = ""
    try: _, existing_sha = fetch_fixed_file(repo, GPT_TO_CLAUDE_PATH, token=token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc): raise
    payload: dict[str, Any] = {"message": f"GPT API reply to Claude mailbox {message_id}", "content": base64.b64encode(content.encode()).decode(), "branch": MAILBOX_BRANCH}
    if existing_sha: payload["sha"] = existing_sha
    _github_json(_content_api(repo, GPT_TO_CLAUDE_PATH), token=token, method="PUT", payload=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge persistent Claude mailbox to bounded GPT API calls.")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select-live"); select.add_argument("--repo", required=True); select.add_argument("--message-output", required=True)
    publish = sub.add_parser("publish-live"); publish.add_argument("--repo", required=True); publish.add_argument("--message-id", required=True); publish.add_argument("--reply-file", required=True)
    args = parser.parse_args(); token = _token_from_env()
    if args.command == "select-live":
        pending, message_id, envelope = select_pending(args.repo, token=token)
        if pending: Path(args.message_output).write_text(envelope, encoding="utf-8")
        print(f"pending={'true' if pending else 'false'}"); print(f"message_id={message_id}"); return 0
    publish_reply(args.repo, token=token, message_id=args.message_id, bus_reply=Path(args.reply_file).read_text(encoding="utf-8", errors="replace")); return 0


if __name__ == "__main__": raise SystemExit(main())
