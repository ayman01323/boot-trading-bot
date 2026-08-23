from __future__ import annotations

import os

from . import ai_cost_router as _cost
from . import ai_council_http_patch as _base


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
        rc, out, err = _base.call_provider(provider, prompt)
    except Exception as exc:
        _cost.finish_call(ticket, success=False, error=f"{type(exc).__name__}: {exc}")
        raise

    ok = int(rc) == 0 and bool(str(out or "").strip())
    _cost.finish_call(ticket, success=ok, error=str(err or "")[:500])
    return int(rc), str(out or ""), str(err or "")


def install() -> None:
    # Keep modules that resolve learnerbot.ai_council.call_provider dynamically on
    # the same budget-gated path. Modules with direct imports use this wrapper
    # explicitly (WebSocket worker and MASTER change council).
    _base._council.call_provider = call_provider


install()
