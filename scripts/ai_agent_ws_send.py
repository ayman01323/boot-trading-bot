from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import time

from websockets.asyncio.client import connect

try:
    from scripts.ai_agent_task_executor import build_task_envelope
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from ai_agent_task_executor import build_task_envelope

AGENTS = {"gpt", "claude", "gemini", "deepseek", "copilot"}
DEFAULT_URL = "ws://127.0.0.1:8765"


def new_message_id(sender: str, target: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{sender}-to-{target}-{stamp}-{secrets.token_hex(2)}"


async def send_and_wait(sender: str, target: str, body: str, message_id: str, timeout: float) -> int:
    url = os.environ.get("AI_AGENT_BUS_URL", DEFAULT_URL)
    token = os.environ.get("AI_AGENT_BUS_TOKEN", "")
    async with connect(url, ping_interval=20, ping_timeout=20, max_size=32_768) as ws:
        await ws.send(json.dumps({"type": "register", "agent": sender, "token": token}, separators=(",", ":")))
        registered = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if registered.get("type") != "registered":
            raise RuntimeError(f"registration failed: {registered}")
        await ws.send(json.dumps({
            "type": "send",
            "message_id": message_id,
            "from": sender,
            "to": target,
            "body": body,
        }, separators=(",", ":"), ensure_ascii=False))
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                print(json.dumps({"message_id": message_id, "status": "TIMEOUT"}))
                return 2
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if data.get("message_id") != message_id:
                continue
            kind = data.get("type")
            if kind in {"accepted", "status"}:
                print(json.dumps(data, ensure_ascii=False))
                continue
            if kind == "reply":
                print(json.dumps(data, ensure_ascii=False))
                return 0 if str(data.get("status") or "").upper() in {"REPLIED", "COMPLETED"} else 1
            if kind == "error":
                print(json.dumps(data, ensure_ascii=False))
                return 1


def _task_body(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    if args.task_action:
        try:
            task_args = json.loads(args.task_args_json or "{}")
        except json.JSONDecodeError as exc:
            parser.error(f"--task-args-json must be valid JSON: {exc}")
        if not isinstance(task_args, dict):
            parser.error("--task-args-json must decode to a JSON object")
        return build_task_envelope(args.task_action, task_args, args.task_instruction or "")
    if not args.message:
        parser.error("provide --message or --task-action")
    return str(args.message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one direct message or bounded task over the local AI-agent WebSocket bus")
    parser.add_argument("--from", dest="sender", required=True, choices=sorted(AGENTS))
    parser.add_argument("--to", dest="target", required=True, choices=sorted(AGENTS))
    parser.add_argument("--message", default="")
    parser.add_argument("--task-action", default="")
    parser.add_argument("--task-args-json", default="{}")
    parser.add_argument("--task-instruction", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    if args.sender == args.target:
        parser.error("sender and target must differ")
    if args.message and args.task_action:
        parser.error("--message and --task-action are mutually exclusive")
    body = _task_body(args, parser)
    message_id = args.message_id or new_message_id(args.sender, args.target)
    return asyncio.run(send_and_wait(args.sender, args.target, body, message_id, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
