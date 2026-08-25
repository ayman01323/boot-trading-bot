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
    already = bool(getattr(_compact, "_kimi_health_roster_installed", False))

    # Always re-assert the roster. Older compatibility modules can still be
    # imported later in test/admin contexts and restore their historical six-agent
    # tuple. Production installs this patch last, but making install() re-entrant
    # keeps explicit diagnostics deterministic as well.
    _compact.PROVIDERS = PROVIDERS
    _compact._LABELS["kimi"] = "Kimi"
    _health.PROVIDERS = PROVIDERS
    _strategy_room.PROVIDERS = PROVIDERS

    _compact._kimi_health_roster_installed = True
    if not already:
        print("[kimi-ai-health] seven_agent_roster=true provider=kimi")


install()
