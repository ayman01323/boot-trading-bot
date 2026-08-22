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

from learnerbot.ai_council_http_patch import call_provider

AGENTS = {"gpt", "claude", "gemini", "deepseek", "copilot"}
DEFAULT_URL = "ws://127.0.0.1:8765"
COPILOT_BUS_BIN_DIR = "/var/tmp/boot-copilot-cli/bin"
CHEAP_MODELS = {
    "gpt": ("OPENAI_COUNCIL_MODEL", "gpt-5-nano"),
    "gemini": ("GEMINI_COUNCIL_MODEL", "gemini-3.1-flash-lite"),
    "claude": ("ANTHROPIC_COUNCIL_MODEL", "claude-haiku-4-5"),
    "deepseek": ("DEEPSEEK_COUNCIL_MODEL", "deepseek-v4-flash"),
}
_PROVIDER_CALL_LOCK = threading.Lock()
_MISSING = object()


def low_cost_model(agent: str) -> str:
    if agent not in CHEAP_MODELS:
        return "provider-default"
    _, default = CHEAP_MODELS[agent]
    override_key = f"AI_BUS_{agent.upper()}_MODEL"
    return str(os.environ.get(override_key) or default).strip()


def build_prompt(agent: str, message: dict[str, Any]) -> str:
    sender = str(message.get("from") or "").upper()
    message_id = str(message.get("message_id") or "")
    body = str(message.get("body") or "")
    return f"""You are {agent.upper()}, a persistent recipient worker on the local AI-agent WebSocket bus.

A new message has been delivered to you automatically. The bus has already acknowledged receipt before this model call.

Sender: {sender}
Message ID: {message_id}

This channel is communication-only. Do not edit files, run shell/Git/GitHub commands, deploy, trade, change LIVE/ARMED/risk/capital settings, access wallets/signing material, reveal secrets, or claim actions you did not perform. Answer the message directly. Keep the response concise (normally no more than 180 words) to minimise API cost. Do not ask another agent unless the sender explicitly requested that.

MESSAGE:
{body[:8000]}
"""


def _restore_env(key: str, previous: object | str) -> None:
    if previous is _MISSING:
        os.environ.pop(key, None)
    else:
        os.environ[key] = str(previous)


def _call_provider_locked(agent: str, prompt: str) -> tuple[int, str, str]:
    # Embedded workers share the learnerbot process with strategy/council logic.
    # Never permanently change the council's model or process PATH. Apply the
    # cheaper bus-specific setting only while this one provider call holds the
    # lock, then restore exactly what the process had before the message arrived.
    with _PROVIDER_CALL_LOCK:
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


async def send_json(ws, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


async def handle_message(ws, agent: str, message: dict[str, Any]) -> None:
    message_id = str(message.get("message_id") or "")
    if str(message.get("to") or "").lower() != agent:
        return
    await send_json(ws, {"type": "ack", "message_id": message_id})
    prompt = build_prompt(agent, message)
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
        "body": answer[:12000],
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
