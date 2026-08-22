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
    "Claude Mailbox Signal": ("claude", "initiation", ".github/ai-mailbox/claude-to-gpt.md", "CLAUDE_TO_GPT"),
    "Gemini Mailbox Signal": ("gemini", "initiation", ".github/ai-mailbox/gemini-init-to-gpt.md", "GEMINI_TO_GPT_INIT"),
}
_REPLY_PATHS = {
    ".github/ai-mailbox/deepseek-to-gpt.md": ("deepseek", "DEEPSEEK_TO_GPT"),
    ".github/ai-mailbox/gemini-to-gpt.md": ("gemini", "GEMINI_TO_GPT"),
    ".github/ai-mailbox/copilot-to-gpt.md": ("copilot", "COPILOT_TO_GPT"),
}
_PROVIDER_RELAY = "AI Mailbox Provider Relay"


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


def _event_from_text(agent: str, kind: str, expected_prefix: str, text: str) -> dict[str, str]:
    first, headers = _headers(text)
    if first != expected_prefix:
        raise ValueError("mailbox prefix mismatch")
    key = "message_id" if kind == "initiation" else "in_reply_to"
    message_id = str(headers.get(key) or "").strip()
    status = str(headers.get("status") or "UNKNOWN").strip().upper()
    # build_message performs the bounded identifier/status validation.
    build_message(agent, kind, message_id, status)
    return {"agent": agent, "kind": kind, "message_id": message_id, "status": status}


def resolve_signal(repo: str, workflow_name: str, head_sha: str, token: str) -> list[dict[str, str]]:
    if workflow_name not in _SIGNAL_SOURCES:
        return []
    agent, kind, path, prefix = _SIGNAL_SOURCES[workflow_name]
    text = _fetch_content(repo, path, head_sha, token)
    return [_event_from_text(agent, kind, prefix, text)]


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
        files = detail.get("files") if isinstance(detail, dict) else []
        for item in files or []:
            path = str((item or {}).get("filename") or "")
            if path not in _REPLY_PATHS:
                continue
            agent, prefix = _REPLY_PATHS[path]
            text = _fetch_content(repo, path, sha, token)
            event = _event_from_text(agent, "reply", prefix, text)
            key = (agent, event["message_id"])
            if key not in seen:
                seen.add(key)
                events.append(event)
    return events


def resolve_events(
    repo: str,
    workflow_name: str,
    head_sha: str,
    started_at: str,
    updated_at: str,
    workflow_event: str,
    token: str,
) -> list[dict[str, str]]:
    if workflow_name in _SIGNAL_SOURCES:
        return resolve_signal(repo, workflow_name, head_sha, token)
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
    events = resolve_events(
        args.repo,
        args.workflow_name,
        args.head_sha,
        args.started_at,
        args.updated_at,
        args.workflow_event,
        github_token,
    )
    print(f"mailbox_events={len(events)}")
    for event in events:
        text = build_message(event["agent"], event["kind"], event["message_id"], event["status"])
        send_telegram(telegram_token, chat_id, text)
        print(f"notified={event['agent']}:{event['kind']}:{event['message_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
