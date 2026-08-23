from __future__ import annotations

"""Translate the internal Strategy Factory service identity onto the audited bus.

The transport/broker intentionally recognises MASTER as the non-agent sender. The
trading-funnel research worker uses the descriptive internal identity
``strategy-factory``; translate only that synthetic sender to ``master`` without
broadening the broker's accepted identities or changing any agent-to-agent flow.
"""

from scripts import strategy_factory_transport as _transport

_PREV_EXCHANGE = _transport.exchange


async def exchange(sender: str, target: str, body: str, **kwargs):
    if str(sender or "").strip().lower() == "strategy-factory":
        sender = "master"
    return await _PREV_EXCHANGE(sender, target, body, **kwargs)


def install() -> None:
    if getattr(_transport, "_strategy_factory_service_sender_patch_installed", False):
        return
    _transport.exchange = exchange
    _transport._strategy_factory_service_sender_patch_installed = True


install()
