from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB = "/var/tmp/boot/ai_agent_bus.sqlite3"
DEFAULT_MAX_EXCHANGES = 6
DEFAULT_MAX_CHARS = 3200
MAX_EXCHANGES_CAP = 12
MAX_CHARS_CAP = 8000


def _enabled() -> bool:
    return str(os.environ.get("AI_BUS_MEMORY_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name) or default).strip()
    try:
        value = int(float(raw))
    except Exception:
        value = default
    return max(low, min(value, high))


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _db_path(explicit: str | None = None) -> str:
    return str(explicit or os.environ.get("AI_AGENT_BUS_DB") or DEFAULT_DB).strip()


def recent_context(
    agent: str,
    *,
    current_message_id: str = "",
    thread_id: str = "",
    db_path: str | None = None,
    max_exchanges: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Return bounded Strategy Factory history.

    Threaded messages read only the named thread, across all participating agents.
    Legacy unthreaded messages retain the older per-agent memory behaviour.
    """
    if not _enabled():
        return ""
    agent = str(agent or "").strip().lower()
    thread_id = str(thread_id or "").strip()
    if not agent:
        return ""
    exchanges = max_exchanges if max_exchanges is not None else _bounded_int("AI_BUS_MEMORY_MAX_EXCHANGES", DEFAULT_MAX_EXCHANGES, 1, MAX_EXCHANGES_CAP)
    char_budget = max_chars if max_chars is not None else _bounded_int("AI_BUS_MEMORY_MAX_CHARS", DEFAULT_MAX_CHARS, 400, MAX_CHARS_CAP)
    exchanges = max(1, min(int(exchanges), MAX_EXCHANGES_CAP))
    char_budget = max(400, min(int(char_budget), MAX_CHARS_CAP))
    path = _db_path(db_path)
    if not path or not Path(path).is_file():
        return ""
    try:
        uri = f"file:{Path(path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            if thread_id:
                rows = list(conn.execute(
                    """SELECT message_id, sender, target, body, reply, subject, replied_at, updated_at
                       FROM messages
                       WHERE status = 'REPLIED' AND reply <> '' AND COALESCE(error, '') = ''
                         AND thread_id = ? AND message_id <> ?
                       ORDER BY COALESCE(replied_at, updated_at) DESC, rowid DESC LIMIT ?""",
                    (thread_id, str(current_message_id or ""), max(exchanges * 3, exchanges)),
                ))
            else:
                rows = list(conn.execute(
                    """SELECT message_id, sender, target, body, reply, '' AS subject, replied_at, updated_at
                       FROM messages
                       WHERE status = 'REPLIED' AND reply <> '' AND COALESCE(error, '') = ''
                         AND (sender = ? OR target = ?) AND message_id <> ?
                       ORDER BY COALESCE(replied_at, updated_at) DESC, rowid DESC LIMIT ?""",
                    (agent, agent, str(current_message_id or ""), max(exchanges * 3, exchanges)),
                ))
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return ""
    selected = list(reversed(rows[:exchanges]))
    blocks: list[str] = []
    for row in selected:
        sender = str(row["sender"] or "").strip().upper()
        target = str(row["target"] or "").strip().upper()
        body = _clip(row["body"], 900)
        reply = _clip(row["reply"], 900)
        if body and reply:
            blocks.append(f"{sender} → {target}: {body}\n{target} → {sender}: {reply}")
    kept: list[str] = []
    used = 0
    for block in reversed(blocks):
        addition = len(block) + (2 if kept else 0)
        if kept and used + addition > char_budget:
            break
        if not kept and addition > char_budget:
            block = block[-char_budget:]
            addition = len(block)
        kept.append(block)
        used += addition
    return "\n\n".join(reversed(kept))
