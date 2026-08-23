from __future__ import annotations

import argparse
import asyncio
import json

from scripts import claude_division as _claude
from scripts.strategy_factory_transport import AGENTS, exchange


def _event_printer(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def _run_general(agent: str, message: str, timeout: float, label: str) -> int:
    result = await exchange(
        "master",
        agent,
        message,
        timeout=timeout,
        on_event=_event_printer,
    )
    result = {**result, "display_agent": label}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("acknowledged") and str(result.get("status") or "").upper() == "REPLIED" else 1


def _run_coding(message: str) -> int:
    result = _claude.publish_coding_request(message, requested_by="MASTER")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chat with a Strategy Factory agent using the canonical MASTER identity"
    )
    choices = tuple(agent for agent in AGENTS if agent != "claude") + ("claude-general", "claude-coding")
    parser.add_argument("agent", choices=choices)
    parser.add_argument("message")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    agent, division = _claude.parse_chat_target(args.agent)
    if agent == "claude" and division == _claude.CODING:
        return _run_coding(args.message)
    if agent == "claude" and division == _claude.GENERAL:
        return asyncio.run(_run_general("claude", _claude.general_message(args.message), args.timeout, "Claude General"))
    return asyncio.run(_run_general(agent, args.message, args.timeout, agent.title()))


if __name__ == "__main__":
    raise SystemExit(main())
