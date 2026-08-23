from __future__ import annotations

import secrets

from . import master_change_council as _base
from scripts.strategy_factory_transport import exchange


async def _ask_one(target: str, request_id: str, body: str, attempt: int, timeout: float = 150.0) -> dict:
    """COUNCIL-mode adapter over the same transport used by DIRECT messaging."""
    message_id = f"master-change-{request_id}-{target}-{attempt}-{secrets.token_hex(2)}"
    result = await exchange(
        "gpt",
        target,
        body,
        message_id=message_id,
        timeout=timeout,
    )
    error = str(result.get("error") or "")[:1200]
    reply = str(result.get("body") or "")[:12000]
    status = str(result.get("status") or "").upper()
    ok = status in {"REPLIED", "COMPLETED"} and not error and bool(reply.strip())
    return {
        "target": target,
        "message_id": message_id,
        "acknowledged": bool(result.get("acknowledged")),
        "provider_rc": 0 if ok else 1,
        "reply": reply,
        "error": error,
        "transport": "strategy-factory-websocket",
        "routing_mode": "COUNCIL",
    }


def install() -> None:
    if getattr(_base, "_strategy_factory_council_transport_installed", False):
        return
    _base._ask_one = _ask_one
    _base._strategy_factory_council_transport_installed = True


install()
