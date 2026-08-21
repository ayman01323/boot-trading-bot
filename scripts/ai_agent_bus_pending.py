from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_MESSAGE_ID_RE = re.compile(r"(?mi)^message_id:\s*([A-Za-z0-9._:-]{1,120})\s*$")


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


def message_id_from_body(body: str) -> str:
    match = _MESSAGE_ID_RE.search(str(body or ""))
    return match.group(1) if match else ""


def replied_message_ids(comments: list[dict[str, Any]]) -> set[str]:
    replied: set[str] = set()
    for row in comments:
        body = str(row.get("body") or "")
        if not body.startswith("AI_BUS_REPLY"):
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
    replied = replied_message_ids(comments)
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


def has_reply(comments: list[dict[str, Any]], message_id: str) -> bool:
    return str(message_id or "").strip() in replied_message_ids(comments)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select/dedupe bounded AI bus issue messages.")
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select")
    select.add_argument("--comments", required=True)
    select.add_argument("--owner", required=True)
    select.add_argument("--message-output", required=True)

    check = sub.add_parser("has-reply")
    check.add_argument("--comments", required=True)
    check.add_argument("--message-id", required=True)

    args = parser.parse_args()
    comments = load_comments(args.comments)

    if args.command == "has-reply":
        return 0 if has_reply(comments, args.message_id) else 1

    selected = latest_pending(comments, owner=args.owner)
    if selected is None:
        print("pending=false")
        print("message_id=none")
        print("comment_id=0")
        return 0

    comment_id, message_id, body = selected
    Path(args.message_output).write_text(body, encoding="utf-8")
    print("pending=true")
    print(f"message_id={message_id}")
    print(f"comment_id={comment_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
