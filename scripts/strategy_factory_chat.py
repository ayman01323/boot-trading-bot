from __future__ import annotations

import argparse
import asyncio
import json

from scripts.strategy_factory_transport import AGENTS, exchange


def _event_printer(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def _run(agent: str, message: str, timeout: float) -> int:
    result = await exchange(
        "master",
        agent,
        message,
        timeout=timeout,
        on_event=_event_printer,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("acknowledged") and str(result.get("status") or "").upper() == "REPLIED" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chat with a persistent Strategy Factory agent using the canonical MASTER identity"
    )
    parser.add_argument("agent", choices=AGENTS)
    parser.add_argument("message")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    return asyncio.run(_run(args.agent, args.message, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
