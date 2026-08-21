from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_MESSAGE_ID_RE = re.compile(r"(?mi)^message_id:\s*([A-Za-z0-9._:-]{1,120})\s*$")
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_API_ROOT = "https://api.github.com"


def _flatten_pages(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    if raw and all(isinstance(row, dict) for row in raw):
        return list(raw)
    rows: list[dict[str, Any]] = []
    for page in raw:
        if isinstance(page, list):
            rows.extend(row for row in page if isinstance(row, dict))
    return rows


def load_comments(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _flatten_pages(raw)


def _token_from_env() -> str:
    return str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _github_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str]]:
    if not token:
        raise RuntimeError("GitHub token is unavailable")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "boot-trading-bot-ai-bus",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return (json.loads(raw) if raw else {}), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {int(exc.code or 500)}: {raw[:600]}") from None
    except Exception as exc:
        raise RuntimeError(f"GitHub API request failed: {type(exc).__name__}") from None


def fetch_issue_comments(repo: str, *, token: str) -> list[dict[str, Any]]:
    repo = str(repo or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository name")
    url = f"{_API_ROOT}/repos/{repo}/issues/333/comments?per_page=100"
    rows: list[dict[str, Any]] = []
    pages = 0
    while url:
        pages += 1
        if pages > 20:
            raise RuntimeError("AI bus comment pagination exceeded safe limit")
        body, headers = _github_json(url, token=token)
        if not isinstance(body, list):
            raise RuntimeError("GitHub comments response was not a list")
        rows.extend(row for row in body if isinstance(row, dict))
        link = str(headers.get("Link") or headers.get("link") or "")
        match = _NEXT_LINK_RE.search(link)
        url = match.group(1) if match else ""
    return rows


def post_issue_reply(repo: str, *, token: str, body: str) -> None:
    repo = str(repo or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository name")
    text = str(body or "").strip()
    if not text.startswith("AI_BUS_REPLY\n"):
        raise ValueError("reply body must start with AI_BUS_REPLY")
    if len(text) > 20000:
        raise ValueError("reply body exceeds safe limit")
    _github_json(
        f"{_API_ROOT}/repos/{repo}/issues/333/comments",
        token=token,
        method="POST",
        payload={"body": text},
    )


def message_id_from_body(body: str) -> str:
    match = _MESSAGE_ID_RE.search(str(body or ""))
    return match.group(1) if match else ""


def replied_message_ids(comments: list[dict[str, Any]], *, owner: str | None = None) -> set[str]:
    trusted = None
    if owner is not None:
        trusted = {str(owner or "").strip(), "github-actions[bot]"}
    replied: set[str] = set()
    for row in comments:
        body = str(row.get("body") or "")
        if not body.startswith("AI_BUS_REPLY"):
            continue
        if trusted is not None:
            user = str(((row.get("user") or {}).get("login") or "")).strip()
            if user not in trusted:
                continue
        message_id = message_id_from_body(body)
        if message_id:
            replied.add(message_id)
    return replied


def latest_pending(
    comments: list[dict[str, Any]],
    *,
    owner: str,
) -> tuple[int, str, str] | None:
    trusted = {str(owner or "").strip(), "github-actions[bot]"}
    replied = replied_message_ids(comments, owner=owner)
    candidates: list[tuple[int, str, str]] = []
    for row in comments:
        body = str(row.get("body") or "")
        if not body.startswith("AI_BUS\n"):
            continue
        user = str(((row.get("user") or {}).get("login") or "")).strip()
        if user not in trusted:
            continue
        message_id = message_id_from_body(body)
        if not message_id or message_id in replied:
            continue
        candidates.append((int(row.get("id") or 0), message_id, body))
    return max(candidates, key=lambda row: row[0]) if candidates else None


def has_reply(comments: list[dict[str, Any]], message_id: str, *, owner: str | None = None) -> bool:
    return str(message_id or "").strip() in replied_message_ids(comments, owner=owner)


def _emit_selection(selected: tuple[int, str, str] | None, message_output: str | Path) -> None:
    if selected is None:
        print("pending=false")
        print("message_id=none")
        print("comment_id=0")
        return
    comment_id, message_id, body = selected
    Path(message_output).write_text(body, encoding="utf-8")
    print("pending=true")
    print(f"message_id={message_id}")
    print(f"comment_id={comment_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Select/dedupe bounded AI bus issue messages.")
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select")
    select.add_argument("--comments", required=True)
    select.add_argument("--owner", required=True)
    select.add_argument("--message-output", required=True)

    live = sub.add_parser("select-live")
    live.add_argument("--repo", required=True)
    live.add_argument("--owner", required=True)
    live.add_argument("--message-output", required=True)

    check = sub.add_parser("has-reply")
    check.add_argument("--comments", required=True)
    check.add_argument("--message-id", required=True)
    check.add_argument("--owner", default=None)

    live_check = sub.add_parser("has-reply-live")
    live_check.add_argument("--repo", required=True)
    live_check.add_argument("--message-id", required=True)
    live_check.add_argument("--owner", required=True)

    post = sub.add_parser("post-reply")
    post.add_argument("--repo", required=True)
    post.add_argument("--body-file", required=True)

    args = parser.parse_args()
    token = _token_from_env()

    if args.command == "post-reply":
        post_issue_reply(args.repo, token=token, body=Path(args.body_file).read_text(encoding="utf-8"))
        return 0

    if args.command == "select-live":
        comments = fetch_issue_comments(args.repo, token=token)
        _emit_selection(latest_pending(comments, owner=args.owner), args.message_output)
        return 0

    if args.command == "has-reply-live":
        comments = fetch_issue_comments(args.repo, token=token)
        return 0 if has_reply(comments, args.message_id, owner=args.owner) else 1

    comments = load_comments(args.comments)
    if args.command == "has-reply":
        return 0 if has_reply(comments, args.message_id, owner=args.owner) else 1

    _emit_selection(latest_pending(comments, owner=args.owner), args.message_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
