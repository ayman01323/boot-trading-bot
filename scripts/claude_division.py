from __future__ import annotations

import base64
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from scripts.claude_git_mailbox_bridge import (
    GPT_TO_CLAUDE_PATH,
    MAILBOX_BRANCH,
    _content_api,
    _github_json,
)

GENERAL = "general"
CODING = "coding"
DIVISIONS = {GENERAL, CODING}

_CLAUDE_ALIASES = {
    "claude-general": GENERAL,
    "claude_general": GENERAL,
    "claude:general": GENERAL,
    "claude-coding": CODING,
    "claude_coding": CODING,
    "claude:coding": CODING,
}
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def parse_chat_target(value: str) -> tuple[str, str]:
    """Return (canonical agent, Claude division).

    Operator-facing chat must never accept bare ``claude`` because the caller
    would not know whether the automated/general or repository/coding division
    was addressed. Other agents have no division.
    """
    raw = str(value or "").strip().lower()
    if raw == "claude":
        raise ValueError("Claude division required: use claude-general or claude-coding")
    division = _CLAUDE_ALIASES.get(raw)
    if division:
        return "claude", division
    return raw, ""


def general_message(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        raise ValueError("message body cannot be empty")
    return (
        "CLAUDE_DIVISION: GENERAL\n"
        "CLAUDE_IDENTITY: AUTOMATED_GENERAL\n"
        "ROUTING_RULE: communication/research/governance only; no repository mutation\n\n"
        + text
    )


def _repo_from_git() -> str:
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return ""
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
    return match.group(1) if match else ""


def repository_name() -> str:
    value = str(os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if value:
        return value
    return _repo_from_git()


def github_token() -> str:
    value = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if value:
        return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True, timeout=8).strip()
    except Exception:
        return ""


def new_coding_message_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"master-to-claude-coding-{stamp}-{secrets.token_hex(2)}"


def _source_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5).strip()
    except Exception:
        return ""


def build_coding_request(
    body: str,
    *,
    message_id: str = "",
    requested_by: str = "MASTER",
    source_sha: str = "",
) -> tuple[str, str]:
    message_id = str(message_id or new_coding_message_id()).strip()
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("invalid Claude Coding message_id")
    text = str(body or "").strip()
    if not text:
        raise ValueError("message body cannot be empty")
    source_sha = str(source_sha or _source_sha()).strip()
    payload = (
        "GPT_TO_CLAUDE\n"
        f"message_id: {message_id}\n"
        "division: CODING\n"
        "identity_required: PERSISTENT_AGENT\n"
        f"requested_by: {str(requested_by or 'MASTER').upper()}\n"
        "status: REQUEST\n"
        + (f"source_sha: {source_sha}\n" if source_sha else "")
        + "constraints: repository/coding division; branch/handoff rules apply; no silent deploy/trade/LIVE/risk/capital/wallet/signing changes; no secrets\n\n"
        + text[:7000]
        + "\n"
    )
    return message_id, payload


def publish_coding_request(
    body: str,
    *,
    message_id: str = "",
    requested_by: str = "MASTER",
    repo: str = "",
    token: str = "",
    source_sha: str = "",
) -> dict[str, str]:
    repo = str(repo or repository_name()).strip()
    token = str(token or github_token()).strip()
    if not repo:
        raise RuntimeError("GitHub repository could not be resolved for Claude Coding")
    if not token:
        raise RuntimeError("GitHub authentication is unavailable for Claude Coding mailbox")

    message_id, content = build_coding_request(
        body,
        message_id=message_id,
        requested_by=requested_by,
        source_sha=source_sha,
    )

    existing_sha = ""
    try:
        current = _github_json(_content_api(repo, GPT_TO_CLAUDE_PATH) + f"?ref={MAILBOX_BRANCH}", token=token)
        if isinstance(current, dict):
            existing_sha = str(current.get("sha") or "").strip()
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise

    request = {
        "message": f"Route {message_id} to Claude Coding",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": MAILBOX_BRANCH,
    }
    if existing_sha:
        request["sha"] = existing_sha
    _github_json(_content_api(repo, GPT_TO_CLAUDE_PATH), token=token, method="PUT", payload=request)
    return {
        "message_id": message_id,
        "division": "CODING",
        "identity_required": "PERSISTENT_AGENT",
        "status": "QUEUED",
        "transport": "GIT_MAILBOX",
        "path": GPT_TO_CLAUDE_PATH,
    }


def coding_reply_identity(text: str) -> tuple[str, str]:
    """Return (division, identity) from a Claude mailbox reply."""
    lines = str(text or "").splitlines()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return str(headers.get("division") or "").upper(), str(headers.get("identity") or "").upper()
