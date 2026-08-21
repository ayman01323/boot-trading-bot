from __future__ import annotations

import time
from pathlib import Path

from . import ai_agent_health_warning_patch as _health_warning
from . import ai_four_agent_health_patch as _health5
from . import telegram_ai_ops_patch as _ai_ops

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "copilot")
_LABELS = {
    "gpt": "GPT",
    "claude": "CLAUDE",
    "gemini": "GEMINI",
    "deepseek": "DEEPSEEK",
    "copilot": "COPILOT",
}

# Keep the five-agent health collectors aligned with the compact display order.
_health5.PROVIDERS = PROVIDERS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _reason(detail: dict) -> str:
    return _health_warning._clean_reason((detail or {}).get("reason"), 320)


def _looks_like_config(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "claude-code",
            "claude code",
            "custom model",
            "model configuration",
            "model config",
            "unsupported model",
            "base_url",
            "base url",
            "anthropic_base_url",
        )
    )


def _looks_like_auth(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "authentication",
            "unauthorized",
            "forbidden",
            "credential",
            "api key",
            "token expired",
            "blocked_auth",
        )
    )


def _looks_like_network(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "connection refused",
            "timed out",
            "timeout",
            "network",
            "dns",
            "unreachable",
            "connection reset",
        )
    )


def _looks_like_report_validation(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "validation",
            "schema",
            "json",
            "parse",
            "format",
            "report is incomplete",
            "report incomplete",
            "invalid report",
        )
    )


def classify_health(lane: str, provider: str, detail: dict) -> str:
    """Convert real health state/reason into the compact operator diagnosis.

    This changes presentation only. It never changes provider state, retry logic,
    master selection, trading state or any execution/safety gate.
    """
    lane = str(lane or "").lower()
    provider = str(provider or "").lower()
    state = str((detail or {}).get("state") or "WAITING").upper()
    reason = _reason(detail)

    if state == "WORKING":
        return "🟢 WORKING"
    if state == "WAITING":
        return "🟡 IN PROGRESS"

    if provider == "deepseek" and _looks_like_config(reason):
        if lane == "strategy":
            return "🔴 SAME CONFIGURATION BUG"
        return "🔴 CLAUDE-CODE CUSTOM MODEL CONFIGURATION"

    if _looks_like_auth(reason):
        return "🔴 AUTHENTICATION FAILURE"
    if _looks_like_network(reason):
        return "🔴 PROVIDER/NETWORK FAILURE"

    # GPT commonly reaches the provider successfully but fails the report/schema
    # validation stage. Keep that distinction visible instead of calling the whole
    # provider down. Claude's non-auth/non-network failures are normally isolated
    # to one workflow lane, so identify them as pipeline-specific.
    if provider == "gpt" or _looks_like_report_validation(reason):
        suffix = " — provider probably reachable" if provider == "gpt" else ""
        return f"🟠 REPORT/VALIDATION FAILURE{suffix}"
    if provider == "claude":
        return "🟠 PIPELINE-SPECIFIC FAILURE"

    # Preserve a concise real diagnosis for other failures without hard-coding a
    # provider as broken when only one lane/report failed.
    cleaned = reason.strip()
    if cleaned and cleaned != "no diagnostic reason was published":
        return f"🟠 PIPELINE-SPECIFIC FAILURE — {cleaned[:120]}"
    return "🟠 PIPELINE-SPECIFIC FAILURE"


def _lane_health(lane: str) -> dict:
    now = int(time.time())
    root = _repo_root()
    if lane == "engineering":
        return _health5._engineering_health(root, now)
    return _health5._strategy_health(root, now)


def _lane_text(lane: str, health: dict | None = None) -> str:
    health = health if health is not None else _lane_health(lane)
    label = "ENGINEERING" if lane == "engineering" else "STRATEGY"
    agents = (health or {}).get("agents") or {}
    lines = [label]
    for provider in PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} report has not completed",
        }
        lines.append(f"{_LABELS[provider]:<9} {classify_health(lane, provider, detail)}")
    return "\n".join(lines)


def engineering_text(_state: dict | None = None) -> str:
    return _lane_text("engineering")


def strategy_text(_state: dict | None = None) -> str:
    return _lane_text("strategy")


def warning_message(snapshot: dict) -> str:
    engineering = (snapshot or {}).get("engineering") or _lane_health("engineering")
    strategy = (snapshot or {}).get("strategy") or _lane_health("strategy")
    return "\n\n".join(
        [
            "🤖 AI AGENT HEALTH REPORT",
            _lane_text("engineering", engineering),
            _lane_text("strategy", strategy),
        ]
    )


def install() -> None:
    # Automatic 30-minute unhealthy warning.
    _health_warning.warning_message = warning_message
    _health5.warning_message = warning_message

    # MASTER /aiaudit, /aistrategy and /aiupdates presentation.
    _health5._engineering_text = engineering_text
    _health5._strategy_text = strategy_text
    _ai_ops._engineering_text = engineering_text
    _ai_ops._strategy_text = strategy_text

    _ai_ops._compact_ai_health_report_installed = True


install()
