from __future__ import annotations

import secrets

from . import master_change_council as _base
from scripts.strategy_factory_transport import exchange


async def _ask_one(target: str, request_id: str, body: str, attempt: int, timeout: float = 150.0) -> dict:
    """COUNCIL-mode adapter over the same transport used by DIRECT messaging.

    All advisers for one MASTER request share its Council thread. Claude in the
    advisory Council is always the GENERAL division; Coding is a separate
    repository implementation identity and is never silently invoked here.
    """
    requested_target = str(target or "").strip().lower()
    routed_target = "claude-general" if requested_target == "claude" else requested_target
    message_id = f"master-change-{request_id}-{requested_target}-{attempt}-{secrets.token_hex(2)}"
    thread_id = f"council-{request_id}"
    subject = f"MASTER change {request_id}"
    result = await exchange(
        "gpt",
        routed_target,
        body,
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        timeout=timeout,
    )
    error = str(result.get("error") or "")[:1200]
    reply = str(result.get("body") or "")[:12000]
    status = str(result.get("status") or "").upper()
    ok = status in {"REPLIED", "COMPLETED"} and not error and bool(reply.strip())
    row = {
        "target": requested_target,
        "message_id": message_id,
        "thread_id": str(result.get("thread_id") or thread_id),
        "subject": str(result.get("subject") or subject),
        "acknowledged": bool(result.get("acknowledged")),
        "provider_rc": 0 if ok else 1,
        "reply": reply,
        "error": error,
        "transport": "strategy-factory-websocket",
        "routing_mode": "COUNCIL",
    }
    if requested_target == "claude":
        row["claude_division"] = "GENERAL"
        row["claude_identity"] = "AUTOMATED_GENERAL"
    return row


def install() -> None:
    if getattr(_base, "_strategy_factory_council_transport_installed", False):
        return
    _base._ask_one = _ask_one
    _base._strategy_factory_council_transport_installed = True


install()
