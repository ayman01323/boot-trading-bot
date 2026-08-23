from __future__ import annotations

from typing import Any

from . import ai_cost_router as _cost

_GROK_RATES = (
    ("grok-build-0.1", 1.0, 2.0, 0.20),
    ("grok-4.20", 1.25, 2.50, 0.20),
    ("grok-4.6", 2.0, 6.0, 0.50),
)

_BASE_MAX_DAILY_CALLS = _cost._max_daily_calls
_BASE_USAGE_FROM_RESPONSE = _cost.usage_from_response


def _max_daily_calls(provider: str) -> int:
    provider = str(provider or "").lower().strip()
    if provider == "grok":
        return max(0, _cost._env_int("AI_COST_GROK_MAX_DAILY_CALLS", 100))
    return _BASE_MAX_DAILY_CALLS(provider)


def usage_from_response(provider: str, body: Any) -> dict[str, int]:
    provider = str(provider or "").lower().strip()
    if provider != "grok":
        return _BASE_USAGE_FROM_RESPONSE(provider, body)
    if not isinstance(body, dict):
        return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    try:
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        return {
            "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            "cached_input_tokens": int(details.get("cached_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        }
    except Exception:
        return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}


def install() -> None:
    if getattr(_cost, "_grok_cost_patch_installed", False):
        return

    # Keep cheap routes L1-L3 unchanged. Grok joins only the full protected
    # council, so adding the sixth family member does not increase routine spend.
    _cost.ALL_ADVISERS = tuple(dict.fromkeys((*_cost.ALL_ADVISERS, "grok")))
    levels = dict(_cost.LEVEL_ADVISERS)
    levels[4] = _cost.ALL_ADVISERS
    _cost.LEVEL_ADVISERS = levels

    existing = tuple(row for row in _cost._MODEL_RATES if not str(row[0]).startswith("grok-"))
    _cost._MODEL_RATES = _GROK_RATES + existing
    _cost._PROVIDER_FALLBACK_RATES["grok"] = (1.25, 2.50, 0.20)
    _cost._max_daily_calls = _max_daily_calls
    _cost.usage_from_response = usage_from_response
    _cost._grok_cost_patch_installed = True


install()
