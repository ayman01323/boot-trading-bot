from __future__ import annotations

import argparse
import asyncio
import json

try:
    from scripts.ai_agent_task_executor import build_task_envelope
    from scripts.strategy_factory_transport import AGENTS, exchange, new_message_id
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from ai_agent_task_executor import build_task_envelope
    from strategy_factory_transport import AGENTS, exchange, new_message_id


def _print_event(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))


async def send_and_wait(sender: str, target: str, body: str, message_id: str, timeout: float) -> int:
    result = await exchange(
        sender,
        target,
        body,
        message_id=message_id,
        timeout=timeout,
        on_event=_print_event,
    )
    status = str(result.get("status") or "").upper()
    return 0 if status in {"REPLIED", "COMPLETED"} and not str(result.get("error") or "") else (2 if status == "TIMEOUT" else 1)


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
    parser = argparse.ArgumentParser(description="Send one DIRECT Strategy Factory message or bounded task over the shared WebSocket transport")
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
