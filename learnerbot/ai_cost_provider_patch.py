from __future__ import annotations

import os

from . import ai_cost_router as _cost
from . import ai_council_http_patch as _base
from . import provider_current_api_patch as _provider_current_api_patch  # noqa: F401
from . import ai_runtime_secret_fallback_patch as _runtime_secret_fallback  # noqa: F401
from . import grok_provider as _grok  # installs raw xAI-compatible routing first
from . import kimi_provider as _kimi  # installs raw Kimi/Moonshot routing after Grok
from . import ai_cost_grok_patch as _grok_cost  # noqa: F401
from . import ai_cost_kimi_patch as _kimi_cost  # noqa: F401

# Preserve the already-installed Grok/Kimi-aware provider implementation before
# replacing the public hook with the budget gate. The wrapper normally calls
# this saved implementation, never itself.
_ORIGINAL_CALL_PROVIDER = _base.call_provider


def _model(provider: str) -> str:
    provider = str(provider or "").lower().strip()
    if provider == "gpt":
        return str(os.environ.get("OPENAI_COUNCIL_MODEL") or "gpt-5.6-terra").strip()
    if provider == "gemini":
        return str(
            os.environ.get("GEMINI_COUNCIL_MODEL")
            or os.environ.get("GEMINI_MASTER_MODEL")
            or os.environ.get("GEMINI_STRATEGY_MODEL")
            or "gemini-3.7-flash"
        ).strip()
    if provider == "claude":
        return str(
            os.environ.get("ANTHROPIC_COUNCIL_MODEL")
            or os.environ.get("CLAUDE_API_MODEL")
            or os.environ.get("CLAUDE_MASTER_MODEL")
            or "claude-sonnet-5"
        ).strip()
    if provider == "deepseek":
        return str(
            os.environ.get("DEEPSEEK_COUNCIL_MODEL")
            or os.environ.get("DEEPSEEK_MASTER_MODEL")
            or "deepseek-v4-flash"
        ).strip()
    if provider == "grok":
        return str(
            os.environ.get("XAI_COUNCIL_MODEL")
            or os.environ.get("GROK_COUNCIL_MODEL")
            or os.environ.get("XAI_MASTER_MODEL")
            or "grok-4.20-non-reasoning"
        ).strip()
    if provider == "kimi":
        return str(
            os.environ.get("KIMI_COUNCIL_MODEL")
            or os.environ.get("MOONSHOT_COUNCIL_MODEL")
            or os.environ.get("KIMI_MASTER_MODEL")
            or "kimi-k2.6"
        ).strip()
    if provider == "copilot":
        return "github-copilot-subscription"
    return "provider-default"


def _estimated_output_tokens(provider: str) -> int:
    provider = str(provider or "").upper().strip()
    # Existing provider adapters permit up to 2400 output tokens. Reserve that
    # worst case by default so a hard budget cannot be crossed by a long reply.
    raw = os.environ.get(f"AI_COST_{provider}_ESTIMATED_OUTPUT_TOKENS") or os.environ.get("AI_COST_ESTIMATED_OUTPUT_TOKENS") or "2400"
    try:
        return max(64, min(int(float(raw)), 2400))
    except Exception:
        return 2400


def _underlying_provider_call(provider: str, prompt: str) -> tuple[int, str, str]:
    # Unit tests and diagnostics historically monkeypatch
    # ai_council_http_patch.call_provider. Honour such an explicit replacement,
    # while avoiding recursion during normal runtime where the public hook is us.
    current = _base.call_provider
    if current is not call_provider:
        return current(provider, prompt)
    return _ORIGINAL_CALL_PROVIDER(provider, prompt)


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    """Budget-gated wrapper around the existing provider implementation.

    The underlying provider API contract remains unchanged. The ledger records a
    conservative estimate because some existing provider adapters do not expose
    token usage through their legacy three-tuple return value.
    """
    provider = str(provider or "").lower().strip()
    model = _model(provider)
    task_kind = str(os.environ.get("AI_COST_TASK_KIND") or "provider-call")[:120]
    try:
        route_level = max(0, min(int(os.environ.get("AI_COST_ROUTE_LEVEL") or 0), 4))
    except Exception:
        route_level = 0
    max_out = _estimated_output_tokens(provider)
    ticket = _cost.reserve_call(
        provider,
        model,
        prompt,
        max_output_tokens=max_out,
        task_kind=task_kind,
        route_level=route_level,
    )
    if not ticket.allowed:
        return 95, "", f"AI Cost Router blocked provider call: {ticket.reason}"

    try:
        rc, out, err = _underlying_provider_call(provider, prompt)
    except Exception as exc:
        _cost.finish_call(ticket, success=False, error=f"{type(exc).__name__}: {exc}")
        raise

    ok = int(rc) == 0 and bool(str(out or "").strip())
    _cost.finish_call(ticket, success=ok, error=str(err or "")[:500])
    return int(rc), str(out or ""), str(err or "")


def install() -> None:
    # Keep the historical invariant required by the existing provider-patch
    # regression: ai_council.call_provider and ai_council_http_patch.call_provider
    # must be the same public function object. The actual Grok/Kimi-aware HTTP
    # implementation is retained privately in _ORIGINAL_CALL_PROVIDER above.
    _base.call_provider = call_provider
    _base._council.call_provider = call_provider


install()
