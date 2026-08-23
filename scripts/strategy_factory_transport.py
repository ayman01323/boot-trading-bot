from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect

from scripts import claude_division as _claude

AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")
SENDERS = AGENTS + ("master",)
PUBLIC_TARGETS = tuple(agent for agent in AGENTS if agent != "claude") + _claude.PUBLIC_CLAUDE_TARGETS
DEFAULT_URL = "ws://127.0.0.1:8765"
MAX_MESSAGE_BYTES = 32_768
MAX_SUBJECT_CHARS = 160
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")

EventCallback = Callable[[dict[str, Any]], None]


def new_message_id(sender: str, target: str, *, prefix: str = "") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_target = str(target or "").replace("_", "-").replace(":", "-")
    base = f"{sender}-to-{safe_target}-{stamp}-{secrets.token_hex(2)}"
    return f"{prefix}-{base}" if prefix else base


def normalise_subject(value: Any) -> str:
    subject = " ".join(str(value or "").split())
    if len(subject) > MAX_SUBJECT_CHARS:
        raise ValueError(f"subject exceeds {MAX_SUBJECT_CHARS} characters")
    return subject


def normalise_thread_id(value: Any) -> str:
    thread_id = str(value or "").strip()
    if thread_id and not THREAD_ID_RE.fullmatch(thread_id):
        raise ValueError("invalid Strategy Factory thread_id")
    return thread_id


def thread_id_for_subject(subject: str) -> str:
    """Return a stable thread id so the same subject can span several agents."""
    subject = normalise_subject(subject)
    if not subject:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", subject.casefold()).strip("-")[:42] or "subject"
    digest = hashlib.sha256(subject.casefold().encode("utf-8")).hexdigest()[:10]
    return f"thr-{slug}-{digest}"


def resolve_thread(*, thread_id: str = "", subject: str = "") -> tuple[str, str]:
    subject = normalise_subject(subject)
    thread_id = normalise_thread_id(thread_id)
    if subject and not thread_id:
        thread_id = thread_id_for_subject(subject)
    return thread_id, subject


def split_subject_message(value: str) -> tuple[str, str]:
    """Parse optional ``[subject] message`` syntax without Telegram dependencies."""
    body = str(value or "").strip()
    if not body.startswith("["):
        return "", body
    end = body.find("]")
    if end <= 1:
        return "", body
    subject = normalise_subject(body[1:end])
    message = body[end + 1 :].strip()
    if not subject or not message:
        return "", body
    return subject, message


def _route_target(target: str, body: str) -> tuple[str, str, str]:
    """Resolve public target to canonical broker target and explicit division.

    ``claude-coding`` deliberately has no WebSocket fallback. It must use the
    persistent Coding mailbox route in ``scripts.claude_division``.
    """
    raw = str(target or "").strip().lower()
    text = str(body or "")
    if raw in {"claude-general", "claude_general", "claude:general"}:
        return "claude", _claude.general_message(text), "GENERAL"
    if raw in {"claude-coding", "claude_coding", "claude:coding"}:
        raise ValueError(
            "Claude Coding is not a Strategy Factory WebSocket recipient; use the persistent Claude Coding mailbox route"
        )
    if raw == "claude":
        # Internal callers may address canonical broker identity only after they
        # explicitly tag the General division. This prevents silent ambiguity.
        if "CLAUDE_DIVISION: GENERAL" not in text.upper():
            raise ValueError("Claude division required: target claude-general or explicitly tag CLAUDE_DIVISION: GENERAL")
        return "claude", text, "GENERAL"
    return raw, text, ""


def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


async def exchange(
    sender: str,
    target: str,
    body: str,
    *,
    message_id: str = "",
    thread_id: str = "",
    subject: str = "",
    timeout: float = 180.0,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Send one Strategy Factory exchange over the single local WebSocket transport.

    DIRECT, COUNCIL and MASTER general-agent chat all use this function. Subject
    threads remain stable across agents. Claude division selection is enforced
    before broker registration so Coding can never silently become General.
    """
    sender = str(sender or "").strip().lower()
    requested_target = str(target or "").strip().lower()
    target, body, claude_division = _route_target(requested_target, str(body or ""))
    if sender not in SENDERS:
        raise ValueError(f"unsupported Strategy Factory sender: {sender}")
    if target not in AGENTS:
        raise ValueError(f"unsupported Strategy Factory target: {requested_target}")
    if sender == target:
        raise ValueError("Strategy Factory sender and target must differ")

    message_id = str(message_id or new_message_id(sender, requested_target or target))
    thread_id, subject = resolve_thread(thread_id=thread_id, subject=subject)
    url = os.environ.get("AI_AGENT_BUS_URL", DEFAULT_URL)
    token = os.environ.get("AI_AGENT_BUS_TOKEN", "")
    result: dict[str, Any] = {
        "message_id": message_id,
        "thread_id": thread_id,
        "subject": subject,
        "from": sender,
        "to": target,
        "requested_to": requested_target,
        "delivered": False,
        "acknowledged": False,
        "status": "QUEUED",
        "body": "",
        "error": "",
    }
    if claude_division:
        result["claude_division"] = claude_division
        result["claude_identity"] = "AUTOMATED_GENERAL"

    async with connect(url, ping_interval=20, ping_timeout=20, max_size=MAX_MESSAGE_BYTES) as ws:
        await ws.send(json.dumps({"type": "register", "agent": sender, "token": token}, separators=(",", ":")))
        registered = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if registered.get("type") != "registered":
            raise RuntimeError(f"Strategy Factory registration failed: {registered}")

        await ws.send(json.dumps({
            "type": "send",
            "message_id": message_id,
            "thread_id": thread_id,
            "subject": subject,
            "from": sender,
            "to": target,
            "body": body,
        }, separators=(",", ":"), ensure_ascii=False))

        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout))
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                result["status"] = "TIMEOUT"
                result["error"] = "timeout waiting for Strategy Factory reply"
                _emit(on_event, {"type": "status", "message_id": message_id, "thread_id": thread_id, "subject": subject, "status": "TIMEOUT", "to": target})
                return result
            try:
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                result["status"] = "TIMEOUT"
                result["error"] = "timeout waiting for Strategy Factory reply"
                _emit(on_event, {"type": "status", "message_id": message_id, "thread_id": thread_id, "subject": subject, "status": "TIMEOUT", "to": target})
                return result

            if data.get("message_id") != message_id:
                continue
            kind = str(data.get("type") or "")
            status = str(data.get("status") or "").upper()
            if "thread_id" in data:
                result["thread_id"] = str(data.get("thread_id") or "")
            if "subject" in data:
                result["subject"] = str(data.get("subject") or "")

            if kind in {"accepted", "status"}:
                if kind == "accepted" or status in {"DELIVERED", "ACKNOWLEDGED", "ACCEPTED", "EXECUTING", "COMPLETED", "REPLIED"}:
                    result["delivered"] = True
                if status in {"ACKNOWLEDGED", "ACCEPTED", "EXECUTING", "COMPLETED", "REPLIED"}:
                    result["acknowledged"] = True
                if status:
                    result["status"] = status
                _emit(on_event, data)
                continue

            if kind == "reply":
                result["delivered"] = True
                result["acknowledged"] = True
                result["status"] = status or "REPLIED"
                result["body"] = str(data.get("body") or "")
                result["error"] = str(data.get("error") or "")
                _emit(on_event, data)
                return result

            if kind == "error":
                result["status"] = status or "FAILED"
                result["error"] = str(data.get("error") or "Strategy Factory bus error")
                _emit(on_event, data)
                return result
