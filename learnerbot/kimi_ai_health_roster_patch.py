from __future__ import annotations

"""Align operator-facing AI health with the seven persistent agent workers.

Presentation/health bookkeeping only. This patch does not change Council quorum,
trading, deployment authority, capital, wallets/signing, LIVE/ARMED state or any
strategy/safety threshold.
"""

from . import ai_four_agent_health_patch as _health
from . import ai_health_compact_report_patch as _compact
from . import strategy_room as _strategy_room

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")


def install() -> None:
    if getattr(_compact, "_kimi_health_roster_installed", False):
        return

    # The compact/mobile Telegram views iterate this tuple dynamically.
    _compact.PROVIDERS = PROVIDERS
    _compact._LABELS["kimi"] = "Kimi"

    # Keep the underlying Engineering/Strategy collectors and Strategy Factory
    # health snapshots on the same seven-agent roster. Missing Kimi evidence is
    # therefore displayed as pending/disconnected rather than silently omitted.
    _health.PROVIDERS = PROVIDERS
    _strategy_room.PROVIDERS = PROVIDERS

    _compact._kimi_health_roster_installed = True
    print("[kimi-ai-health] seven_agent_roster=true provider=kimi")


install()
