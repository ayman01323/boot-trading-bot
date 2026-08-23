from __future__ import annotations

import argparse
import asyncio
import json

from scripts import claude_division as _claude
from scripts.strategy_factory_transport import PUBLIC_TARGETS, exchange, resolve_thread


def _event_printer(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def _run(agent: str, message: str, timeout: float, *, thread_id: str = "", subject: str = "") -> int:
    thread_id, subject = resolve_thread(thread_id=thread_id, subject=subject)
    if agent == "claude-coding":
        result = _claude.publish_coding_request(
            message,
            requested_by="MASTER",
            thread_id=thread_id,
            subject=subject,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    result = await exchange(
        "master",
        agent,
        message,
        thread_id=thread_id,
        subject=subject,
        timeout=timeout,
        on_event=_event_printer,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("acknowledged") and str(result.get("status") or "").upper() == "REPLIED" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chat with an explicit Strategy Factory agent or Claude Coding persistent mailbox identity"
    )
    parser.add_argument("agent", choices=PUBLIC_TARGETS)
    parser.add_argument("message")
    parser.add_argument("--subject", default="", help="Human-readable subject. Same subject automatically maps to the same thread.")
    parser.add_argument("--thread-id", default="", help="Explicit Strategy Factory thread id.")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    return asyncio.run(_run(args.agent, args.message, args.timeout, thread_id=args.thread_id, subject=args.subject))


if __name__ == "__main__":
    raise SystemExit(main())
