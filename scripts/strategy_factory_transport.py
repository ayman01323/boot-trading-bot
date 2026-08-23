from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect

AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")
SENDERS = AGENTS + ("master",)
DEFAULT_URL = "ws://127.0.0.1:8765"
MAX_MESSAGE_BYTES = 32_768

EventCallback = Callable[[dict[str, Any]], None]


def new_message_id(sender: str, target: str, *, prefix: str = "") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = f"{sender}-to-{target}-{stamp}-{secrets.token_hex(2)}"
    return f"{prefix}-{base}" if prefix else base


def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


async def exchange(
    sender: str,
    target: str,
    body: str,
    *,
    message_id: str = "",
    timeout: float = 180.0,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Send one Strategy Factory exchange over the single local WebSocket transport.

    DIRECT, COUNCIL and the MASTER interactive chat entrypoint all use this
    function. It owns registration, delivery/ACK correlation, timeout handling
    and the final reply contract so those modes cannot silently drift into
    separate messaging implementations.
    """
    sender = str(sender or "").strip().lower()
    target = str(target or "").strip().lower()
    if sender not in SENDERS:
        raise ValueError(f"unsupported Strategy Factory sender: {sender}")
    if target not in AGENTS:
        raise ValueError(f"unsupported Strategy Factory target: {target}")
    if sender == target:
        raise ValueError("Strategy Factory sender and target must differ")

    message_id = str(message_id or new_message_id(sender, target))
    url = os.environ.get("AI_AGENT_BUS_URL", DEFAULT_URL)
    token = os.environ.get("AI_AGENT_BUS_TOKEN", "")
    result: dict[str, Any] = {
        "message_id": message_id,
        "from": sender,
        "to": target,
        "delivered": False,
        "acknowledged": False,
        "status": "QUEUED",
        "body": "",
        "error": "",
    }

    async with connect(url, ping_interval=20, ping_timeout=20, max_size=MAX_MESSAGE_BYTES) as ws:
        await ws.send(json.dumps({"type": "register", "agent": sender, "token": token}, separators=(",", ":")))
        registered = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if registered.get("type") != "registered":
            raise RuntimeError(f"Strategy Factory registration failed: {registered}")

        await ws.send(json.dumps({
            "type": "send",
            "message_id": message_id,
            "from": sender,
            "to": target,
            "body": str(body or ""),
        }, separators=(",", ":"), ensure_ascii=False))

        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout))
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                result["status"] = "TIMEOUT"
                result["error"] = "timeout waiting for Strategy Factory reply"
                _emit(on_event, {"type": "status", "message_id": message_id, "status": "TIMEOUT", "to": target})
                return result
            try:
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                result["status"] = "TIMEOUT"
                result["error"] = "timeout waiting for Strategy Factory reply"
                _emit(on_event, {"type": "status", "message_id": message_id, "status": "TIMEOUT", "to": target})
                return result

            if data.get("message_id") != message_id:
                continue
            kind = str(data.get("type") or "")
            status = str(data.get("status") or "").upper()

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
