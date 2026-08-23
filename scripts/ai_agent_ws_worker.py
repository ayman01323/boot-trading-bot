from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from learnerbot.ai_cost_provider_patch import call_provider
from scripts.ai_agent_task_executor import TaskError, execute_task, parse_task_envelope
from scripts.ai_agent_ws_memory import recent_context

AGENTS = {"gpt", "claude", "gemini", "deepseek", "copilot"}
DEFAULT_URL = "ws://127.0.0.1:8765"
COPILOT_BUS_BIN_DIR = "/var/tmp/boot-copilot-cli/bin"
CHEAP_MODELS = {
    "gpt": ("OPENAI_COUNCIL_MODEL", "gpt-5-nano"),
    "gemini": ("GEMINI_COUNCIL_MODEL", "gemini-3.5-flash-lite"),
    "claude": ("ANTHROPIC_COUNCIL_MODEL", "claude-haiku-4-5"),
    "deepseek": ("DEEPSEEK_COUNCIL_MODEL", "deepseek-v4-flash"),
}
_RETIRED_MODEL_ALIASES = {
    "gemini": {
        "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
        "models/gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    },
}
_PROVIDER_CALL_LOCK = threading.Lock()
_MISSING = object()
MAX_REPLY_CHARS = 7600


def low_cost_model(agent: str) -> str:
    if agent not in CHEAP_MODELS:
        return "provider-default"
    _, default = CHEAP_MODELS[agent]
    override_key = f"AI_BUS_{agent.upper()}_MODEL"
    raw = str(os.environ.get(override_key) or default).strip()
    aliases = _RETIRED_MODEL_ALIASES.get(agent, {})
    return aliases.get(raw.lower(), raw)


def build_prompt(agent: str, message: dict[str, Any], memory: str = "") -> str:
    sender = str(message.get("from") or "").upper()
    message_id = str(message.get("message_id") or "")
    body = str(message.get("body") or "")
    memory = str(memory or "").strip()
    memory_section = ""
    if memory:
        memory_section = f"""
RECENT STRATEGY FACTORY CONVERSATION MEMORY:
{memory}

The memory above is bounded historical context recovered from this system's durable WebSocket audit database. It may contain conversations with GPT or other Strategy Factory agents. It does NOT imply access to separate external web-chat sessions (for example, a Gemini website chat) unless those messages were explicitly bridged into Strategy Factory. Treat the current message as authoritative if it conflicts with older context.
"""

    return f"""You are {agent.upper()}, a persistent recipient worker on the local AI-agent WebSocket bus.

A new communication message has been delivered to you automatically. The bus has already acknowledged receipt before this model call.

Sender: {sender}
Message ID: {message_id}
{memory_section}
This message is communication-only. Do not edit files, run shell/Git/GitHub commands, deploy, trade, change LIVE/ARMED/risk/capital settings, access wallets/signing material, reveal secrets, or claim actions you did not perform. Safe deterministic execution is available only through a structured ws-bus-v2 task envelope handled outside the model. Answer this message directly. Keep the response concise (normally no more than 180 words) to minimise API cost. Do not ask another agent unless the sender explicitly requested that.

MESSAGE:
{body[:8000]}
"""


def _restore_env(key: str, previous: object | str) -> None:
    if previous is _MISSING:
        os.environ.pop(key, None)
    else:
        os.environ[key] = str(previous)


def _call_provider_locked(agent: str, prompt: str) -> tuple[int, str, str]:
    with _PROVIDER_CALL_LOCK:
        previous_kind: object | str = os.environ.get("AI_COST_TASK_KIND", _MISSING)
        previous_level: object | str = os.environ.get("AI_COST_ROUTE_LEVEL", _MISSING)
        os.environ["AI_COST_TASK_KIND"] = "ws-message"
        os.environ["AI_COST_ROUTE_LEVEL"] = "1"
        try:
            if agent == "copilot":
                previous_path: object | str = os.environ.get("PATH", _MISSING)
                configured_dir = str(os.environ.get("AI_BUS_COPILOT_BIN_DIR") or COPILOT_BUS_BIN_DIR).strip()
                copilot_bin = Path(configured_dir) / "copilot"
                if copilot_bin.is_file() and os.access(copilot_bin, os.X_OK):
                    current_path = str(os.environ.get("PATH") or "")
                    os.environ["PATH"] = configured_dir + (os.pathsep + current_path if current_path else "")
                try:
                    return call_provider(agent, prompt)
                finally:
                    _restore_env("PATH", previous_path)

            if agent not in CHEAP_MODELS:
                return call_provider(agent, prompt)

            env_key, _ = CHEAP_MODELS[agent]
            model = low_cost_model(agent)
            previous: object | str = os.environ.get(env_key, _MISSING)
            os.environ[env_key] = model
            try:
                return call_provider(agent, prompt)
            finally:
                _restore_env(env_key, previous)
        finally:
            _restore_env("AI_COST_TASK_KIND", previous_kind)
            _restore_env("AI_COST_ROUTE_LEVEL", previous_level)


async def send_json(ws, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def _compact_task_result(result: dict[str, Any]) -> str:
    raw = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    if len(raw) <= MAX_REPLY_CHARS:
        return raw
    evidence = result.get("evidence") or {}
    compact: dict[str, Any] = {}
    if isinstance(evidence, dict):
        for key, value in evidence.items():
            if isinstance(value, str):
                compact[key] = value[-2200:]
            elif isinstance(value, list):
                compact[key] = value[:20]
            else:
                compact[key] = value
    reduced = {**result, "evidence": compact, "truncated": True}
    raw = json.dumps(reduced, separators=(",", ":"), ensure_ascii=False)
    if len(raw) <= MAX_REPLY_CHARS:
        return raw
    reduced["evidence"] = {"truncated": True, "detail": raw[:4200]}
    raw = json.dumps(reduced, separators=(",", ":"), ensure_ascii=False)
    if len(raw) <= MAX_REPLY_CHARS:
        return raw
    fallback = {
        "protocol": result.get("protocol", "ws-bus-v2"),
        "kind": "task_result",
        "status": result.get("status", "FAILED"),
        "action": result.get("action", ""),
        "summary": str(result.get("summary") or "")[:1000],
        "evidence": {"truncated": True},
        "error": str(result.get("error") or "")[:800],
    }
    return json.dumps(fallback, separators=(",", ":"), ensure_ascii=False)


async def _handle_task(ws, message_id: str, task: dict[str, Any]) -> None:
    await send_json(ws, {"type": "progress", "message_id": message_id, "status": "ACCEPTED"})
    await send_json(ws, {"type": "progress", "message_id": message_id, "status": "EXECUTING"})
    result = await asyncio.to_thread(execute_task, task)
    status = str(result.get("status") or "FAILED").upper()
    await send_json(ws, {
        "type": "reply",
        "message_id": message_id,
        "status": status,
        "body": _compact_task_result(result),
        "error": str(result.get("error") or "")[:1200] if status != "COMPLETED" else "",
        "provider_rc": 0,
        "duration_ms": 0,
    })


async def handle_message(ws, agent: str, message: dict[str, Any]) -> None:
    message_id = str(message.get("message_id") or "")
    if str(message.get("to") or "").lower() != agent:
        return
    await send_json(ws, {"type": "ack", "message_id": message_id})

    body = str(message.get("body") or "")
    try:
        task = parse_task_envelope(body)
    except TaskError as exc:
        result = {
            "protocol": "ws-bus-v2",
            "kind": "task_result",
            "status": "REJECTED",
            "action": "",
            "summary": "Malformed task envelope was rejected.",
            "evidence": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
        await send_json(ws, {
            "type": "reply",
            "message_id": message_id,
            "status": "REJECTED",
            "body": _compact_task_result(result),
            "error": result["error"][:1200],
        })
        return

    if task is not None:
        await _handle_task(ws, message_id, task)
        return

    memory = await asyncio.to_thread(recent_context, agent, current_message_id=message_id)
    prompt = build_prompt(agent, message, memory)
    started = time.monotonic()
    rc, out, err = await asyncio.to_thread(_call_provider_locked, agent, prompt)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    answer = str(out or "").strip()
    error = str(err or "").strip()
    if int(rc) != 0 or not answer:
        answer = f"{agent.upper()} provider call was blocked (rc={int(rc)})."
        if error:
            answer += " " + error[:800]
    await send_json(ws, {
        "type": "reply",
        "message_id": message_id,
        "status": "REPLIED",
        "body": answer[:MAX_REPLY_CHARS],
        "error": error[:1200] if int(rc) != 0 else "",
        "provider_rc": int(rc),
        "duration_ms": elapsed_ms,
    })


async def run(agent: str, url: str, token: str) -> None:
    model = low_cost_model(agent)
    delay = 1.0
    while True:
        try:
            async with connect(url, ping_interval=20, ping_timeout=20, max_size=32_768) as ws:
                await send_json(ws, {"type": "register", "agent": agent, "token": token})
                registered = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if registered.get("type") != "registered":
                    raise RuntimeError(f"registration failed: {registered}")
                print(f"{agent} worker connected; model={model}", flush=True)
                delay = 1.0
                async for raw in ws:
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("type") == "message":
                        await handle_message(ws, agent, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"{agent} worker disconnected: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(delay + random.uniform(0.0, 0.25))
            delay = min(delay * 2.0, 30.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent recipient worker for the local AI-agent WebSocket bus")
    parser.add_argument("--agent", required=True, choices=sorted(AGENTS))
    parser.add_argument("--url", default=os.environ.get("AI_AGENT_BUS_URL", DEFAULT_URL))
    args = parser.parse_args()
    asyncio.run(run(args.agent, args.url, os.environ.get("AI_AGENT_BUS_TOKEN", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
