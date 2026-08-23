from __future__ import annotations

from typing import Any

from . import ai_cost_router as _cost

# Conservative USD guardrail estimates. Kimi's platform prices are denominated
# in CNY and change independently of this repository, so every value remains
# overrideable through AI_COST_PRICE_KIMI_*_PER_MTOK.
_KIMI_RATES = (
    ("kimi-k2.6", 1.00, 5.00, 0.20),
)

_BASE_MAX_DAILY_CALLS = _cost._max_daily_calls
_BASE_USAGE_FROM_RESPONSE = _cost.usage_from_response


def _max_daily_calls(provider: str) -> int:
    provider = str(provider or "").lower().strip()
    if provider == "kimi":
        return max(0, _cost._env_int("AI_COST_KIMI_MAX_DAILY_CALLS", 100))
    return _BASE_MAX_DAILY_CALLS(provider)


def usage_from_response(provider: str, body: Any) -> dict[str, int]:
    provider = str(provider or "").lower().strip()
    if provider != "kimi":
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
    if getattr(_cost, "_kimi_cost_patch_installed", False):
        return

    # Stage Kimi as an individually callable Strategy Factory/Council provider
    # without altering the existing protected MASTER-change adviser quorum. Once
    # a real Kimi credential has passed the live diagnostic, a separate bounded
    # change can add it to ALL_ADVISERS/Level 4 without making missing credentials
    # block production governance.
    existing = tuple(row for row in _cost._MODEL_RATES if not str(row[0]).startswith("kimi-"))
    _cost._MODEL_RATES = _KIMI_RATES + existing
    _cost._PROVIDER_FALLBACK_RATES["kimi"] = (1.00, 5.00, 0.20)
    _cost._max_daily_calls = _max_daily_calls
    _cost.usage_from_response = usage_from_response
    _cost._kimi_cost_patch_installed = True


install()
