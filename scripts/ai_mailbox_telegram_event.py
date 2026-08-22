from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from scripts.ai_mailbox_telegram_notify import build_message, send_telegram

_SIGNAL_SOURCES = {
    "Claude Mailbox Signal": (
        "claude", "initiation", ".github/ai-mailbox/claude-to-gpt.md", "CLAUDE_TO_GPT", "PERSISTENT_AGENT"
    ),
    "Gemini Mailbox Signal": (
        "gemini", "initiation", ".github/ai-mailbox/gemini-init-to-gpt.md", "GEMINI_TO_GPT_INIT", "AGENT_MAILBOX"
    ),
    "Claude API Mailbox Signal": (
        "claude", "api_reply", ".github/ai-mailbox/claude-api-to-gpt.md", "CLAUDE_API_TO_GPT", "STATELESS_API_RESPONDER"
    ),
}
_REPLY_PATHS = {
    ".github/ai-mailbox/deepseek-to-gpt.md": ("deepseek", "DEEPSEEK_TO_GPT"),
    ".github/ai-mailbox/gemini-to-gpt.md": ("gemini", "GEMINI_TO_GPT"),
    ".github/ai-mailbox/copilot-to-gpt.md": ("copilot", "COPILOT_TO_GPT"),
}
_PROVIDER_REQUEST_PATHS = {
    ".github/ai-mailbox/gpt-to-deepseek.md": ("deepseek", "GPT_TO_DEEPSEEK"),
    ".github/ai-mailbox/gpt-to-gemini.md": ("gemini", "GPT_TO_GEMINI"),
    ".github/ai-mailbox/gpt-to-copilot.md": ("copilot", "GPT_TO_COPILOT"),
}
_PROVIDER_RELAY = "AI Mailbox Provider Relay"
_PROVIDER_SIGNAL = "AI Mailbox Provider Signal"
_GPT_CLAUDE_SIGNAL = "GPT Mailbox Signal"


def _parse_iso(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _github_json(url: str, token: str) -> object:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is unavailable")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "boot-ai-mailbox-telegram-event",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _headers(text: str) -> tuple[str, dict[str, str]]:
    lines = str(text or "").splitlines()
    first = lines[0].strip() if lines else ""
    values: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
    return first, values


def _fetch_content(repo: str, path: str, ref: str, token: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    body = _github_json(f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={encoded_ref}", token)
    if not isinstance(body, dict):
        raise RuntimeError("GitHub content response was not an object")
    encoded = str(body.get("content") or "").replace("\n", "")
    return base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")


def _event_from_text(agent: str, kind: str, expected_prefix: str, text: str, identity: str = "") -> dict[str, str]:
    first, headers = _headers(text)
    if first != expected_prefix:
        raise ValueError("mailbox prefix mismatch")
    if kind in {"reply", "api_reply"}:
        key = "in_reply_to"
    elif kind == "delivery":
        key = "message_id" if headers.get("message_id") else "in_reply_to"
    else:
        key = "message_id"
    message_id = str(headers.get(key) or "").strip()
    status = str(headers.get("status") or "UNKNOWN").strip().upper()
    actual_identity = str(headers.get("identity") or identity or "").strip().upper()
    build_message(agent, kind, message_id, status, actual_identity)
    return {
        "agent": agent,
        "kind": kind,
        "message_id": message_id,
        "status": status,
        "identity": actual_identity,
    }


def resolve_signal(repo: str, workflow_name: str, head_sha: str, token: str) -> list[dict[str, str]]:
    if workflow_name not in _SIGNAL_SOURCES:
        return []
    agent, kind, path, prefix, identity = _SIGNAL_SOURCES[workflow_name]
    text = _fetch_content(repo, path, head_sha, token)
    return [_event_from_text(agent, kind, prefix, text, identity)]


def resolve_gpt_claude_delivery(repo: str, head_sha: str, token: str) -> list[dict[str, str]]:
    text = _fetch_content(repo, ".github/ai-mailbox/gpt-to-claude.md", head_sha, token)
    first, headers = _headers(text)
    if first != "GPT_TO_CLAUDE":
        return []
    identity = "STATELESS_API_TARGET" if str(headers.get("status") or "").upper() == "REQUEST" else "GPT_API_RESPONDER"
    return [_event_from_text("claude", "delivery", "GPT_TO_CLAUDE", text, identity)]


def resolve_provider_deliveries(repo: str, head_sha: str, token: str) -> list[dict[str, str]]:
    detail = _github_json(f"https://api.github.com/repos/{repo}/commits/{head_sha}", token)
    if not isinstance(detail, dict):
        return []
    events: list[dict[str, str]] = []
    for item in detail.get("files") or []:
        path = str((item or {}).get("filename") or "")
        if path not in _PROVIDER_REQUEST_PATHS:
            continue
        agent, prefix = _PROVIDER_REQUEST_PATHS[path]
        text = _fetch_content(repo, path, head_sha, token)
        events.append(_event_from_text(agent, "delivery", prefix, text, "STATELESS_API_TARGET"))
    return events


def resolve_provider_replies(repo: str, started_at: str, updated_at: str, token: str) -> list[dict[str, str]]:
    start = _parse_iso(started_at) - timedelta(seconds=2)
    end = _parse_iso(updated_at) + timedelta(seconds=2)
    since = urllib.parse.quote(start.isoformat().replace("+00:00", "Z"), safe="")
    until = urllib.parse.quote(end.isoformat().replace("+00:00", "Z"), safe="")
    commits = _github_json(
        f"https://api.github.com/repos/{repo}/commits?sha=ai-mailbox&since={since}&until={until}&per_page=100",
        token,
    )
    if not isinstance(commits, list):
        raise RuntimeError("GitHub commits response was not a list")
    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in reversed(commits):
        sha = str((row or {}).get("sha") or "").strip()
        if not sha:
            continue
        detail = _github_json(f"https://api.github.com/repos/{repo}/commits/{sha}", token)
        if not isinstance(detail, dict):
            continue
        commit = detail.get("commit") or {}
        commit_message = str(commit.get("message") or "").splitlines()[0].strip() if isinstance(commit, dict) else ""
        for item in detail.get("files") or []:
            path = str((item or {}).get("filename") or "")
            if path not in _REPLY_PATHS:
                continue
            agent, prefix = _REPLY_PATHS[path]
            expected_commit_prefix = f"{agent.title()} to GPT mailbox "
            if not commit_message.startswith(expected_commit_prefix):
                continue
            text = _fetch_content(repo, path, sha, token)
            event = _event_from_text(agent, "reply", prefix, text, "STATELESS_API_RESPONDER")
            key = (agent, event["message_id"])
            if key not in seen:
                seen.add(key)
                events.append(event)
    return events


def resolve_events(repo: str, workflow_name: str, head_sha: str, started_at: str, updated_at: str, workflow_event: str, token: str) -> list[dict[str, str]]:
    if workflow_name in _SIGNAL_SOURCES:
        return resolve_signal(repo, workflow_name, head_sha, token)
    if workflow_name == _GPT_CLAUDE_SIGNAL:
        return resolve_gpt_claude_delivery(repo, head_sha, token)
    if workflow_name == _PROVIDER_SIGNAL:
        return resolve_provider_deliveries(repo, head_sha, token)
    if workflow_name == _PROVIDER_RELAY and workflow_event == "workflow_run":
        return resolve_provider_replies(repo, started_at, updated_at, token)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a completed mailbox workflow and send Telegram MASTER metadata alerts.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--updated-at", default="")
    parser.add_argument("--workflow-event", default="")
    parser.add_argument("--skip-if-unconfigured", action="store_true")
    args = parser.parse_args()
    telegram_token = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(os.environ.get("TELEGRAM_MASTER_CHAT_ID") or "").strip()
    if args.skip_if_unconfigured and (not telegram_token or not chat_id):
        print("telegram_configured=false")
        return 0
    github_token = str(os.environ.get("GITHUB_TOKEN") or "").strip()
    events = resolve_events(args.repo, args.workflow_name, args.head_sha, args.started_at, args.updated_at, args.workflow_event, github_token)
    print(f"mailbox_events={len(events)}")
    for event in events:
        text = build_message(event["agent"], event["kind"], event["message_id"], event["status"], event.get("identity", ""))
        send_telegram(telegram_token, chat_id, text)
        print(f"notified={event['agent']}:{event['kind']}:{event['message_id']}:{event.get('identity','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
