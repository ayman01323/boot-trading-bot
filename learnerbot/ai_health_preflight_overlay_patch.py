from __future__ import annotations

import time
from pathlib import Path

from . import ai_agent_health_warning_patch as _warning
from . import ai_four_agent_health_patch as _health6
from . import ai_health_compact_report_patch as _compact

# Provider API preflight names differ from the Telegram/Council names.
_PREFLIGHT_KEYS = {
    "gpt": "openai",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "grok": "xai",
}
_PREFLIGHT_MAX_AGE_SECONDS = 20 * 60
_STALE_REVIEW_SECONDS = 30 * 60

_ORIGINAL_ENGINEERING_HEALTH = _health6._engineering_health
_ORIGINAL_STRATEGY_HEALTH = _health6._strategy_health
_ORIGINAL_CLASSIFY_HEALTH = _compact.classify_health


def _fresh_preflight(root: Path, now: int) -> dict:
    value = _warning.read_json(root, "health/provider_api_preflight.json") or {}
    try:
        checked = int(value.get("checked_epoch") or 0)
    except Exception:
        return {}
    age = max(0, now - checked) if checked else 10**9
    if age > _PREFLIGHT_MAX_AGE_SECONDS:
        return {}
    return value


def _overlay_stale_review(health: dict, *, lane: str, root: Path, now: int) -> dict:
    """Do not report an old review failure as a current provider outage.

    A fresh provider preflight is only allowed to soften a stale review-cycle
    NOT_WORKING state to WAITING/Refreshing. It never turns it green, and it
    never masks a failure in a current review cycle. That preserves visibility
    of genuine pipeline failures while preventing days-old model/auth errors
    from being presented as current provider health.
    """
    out = dict(health or {})
    try:
        review_age = int(out.get("age_seconds") or 0)
    except Exception:
        review_age = 0
    if review_age < _STALE_REVIEW_SECONDS:
        return out

    preflight = _fresh_preflight(root, now)
    if not preflight:
        return out

    agents = {name: dict(detail or {}) for name, detail in ((out.get("agents") or {}).items())}
    changed = False
    for provider, preflight_key in _PREFLIGHT_KEYS.items():
        detail = agents.get(provider)
        if not isinstance(detail, dict):
            continue
        if str(detail.get("state") or "").upper() != "NOT_WORKING":
            continue
        live = preflight.get(preflight_key) or {}
        if str(live.get("state") or "").upper() != "WORKING":
            continue
        detail["state"] = "WAITING"
        detail["reason"] = (
            f"Provider API is healthy in the latest preflight; the {lane} review "
            "result is stale and a replacement report is being refreshed"
        )
        detail["provider_preflight"] = "WORKING"
        changed = True

    if changed:
        out["agents"] = agents
        out["valid_count"] = sum(
            1 for detail in agents.values()
            if str((detail or {}).get("state") or "").upper() == "WORKING"
        )
    return out


def engineering_health(root: Path, now: int) -> dict:
    base = _ORIGINAL_ENGINEERING_HEALTH(root, now)
    return _overlay_stale_review(base, lane="engineering", root=Path(root), now=now)


def strategy_health(root: Path, now: int) -> dict:
    base = _ORIGINAL_STRATEGY_HEALTH(root, now)
    return _overlay_stale_review(base, lane="strategy", root=Path(root), now=now)


def classify_health(lane: str, provider: str, detail: dict) -> tuple[str, str]:
    state = str((detail or {}).get("state") or "WAITING").upper()
    reason = str((detail or {}).get("reason") or "").lower()
    if state == "WAITING" and (
        str((detail or {}).get("provider_preflight") or "").upper() == "WORKING"
        or "replacement report is being refreshed" in reason
    ):
        return "🟡", "Refreshing"
    return _ORIGINAL_CLASSIFY_HEALTH(lane, provider, detail)


def install() -> None:
    if getattr(_compact, "_preflight_health_overlay_installed", False):
        return

    # All health entry points must see the same reconciled state: Telegram
    # drill-down, compact dashboard and automatic unhealthy-warning loop.
    _health6._engineering_health = engineering_health
    _health6._strategy_health = strategy_health
    _warning._engineering_health = engineering_health
    _warning._strategy_health = strategy_health
    _compact.classify_health = classify_health
    _compact._preflight_health_overlay_installed = True


install()

# Final presentation only: show provider/agent health separately from the review
# pipeline state so Telegram matches the operator-facing explanation.
from . import telegram_ai_health_truth_patch as _truth_view  # noqa: E402,F401
