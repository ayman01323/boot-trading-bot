from __future__ import annotations

import secrets

from scripts import claude_division as _claude
from scripts.strategy_factory_transport import exchange

from . import master_change_council as _base


async def _ask_one(target: str, request_id: str, body: str, attempt: int, timeout: float = 150.0) -> dict:
    """COUNCIL-mode adapter over the same transport used by DIRECT messaging.

    Claude council advice is explicitly CLAUDE GENERAL. Repository implementation
    must use the separate Claude Coding handoff path rather than silently
    substituting the automated general provider for the coding division.
    """
    message_id = f"master-change-{request_id}-{target}-{attempt}-{secrets.token_hex(2)}"
    division = ""
    outbound = body
    if str(target or "").lower() == "claude":
        division = "GENERAL"
        outbound = _claude.general_message(body)
    result = await exchange(
        "gpt",
        target,
        outbound,
        message_id=message_id,
        timeout=timeout,
    )
    error = str(result.get("error") or "")[:1200]
    reply = str(result.get("body") or "")[:12000]
    status = str(result.get("status") or "").upper()
    ok = status in {"REPLIED", "COMPLETED"} and not error and bool(reply.strip())
    row = {
        "target": target,
        "message_id": message_id,
        "acknowledged": bool(result.get("acknowledged")),
        "provider_rc": 0 if ok else 1,
        "reply": reply,
        "error": error,
        "transport": "strategy-factory-websocket",
        "routing_mode": "COUNCIL",
    }
    if division:
        row["claude_division"] = division
        row["claude_identity"] = "AUTOMATED_GENERAL"
    return row


def install() -> None:
    if getattr(_base, "_strategy_factory_council_transport_installed", False):
        return
    _base._ask_one = _ask_one
    _base._strategy_factory_council_transport_installed = True


install()
