from __future__ import annotations

import re
import sys
import types
from pathlib import Path

# This module is used by communication-only AI bus/mailbox workflows that run
# in deliberately tiny virtual environments. Importing ``learnerbot`` normally
# executes learnerbot/__init__.py, which bootstraps the trading runtime and pulls
# in dependencies such as Web3 that these message relays neither need nor should
# load. Create only the package namespace so the provider modules can use their
# normal relative imports without executing the trading package initialiser.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LEARNERBOT_DIR = _REPO_ROOT / "learnerbot"
if "learnerbot" not in sys.modules:
    package = types.ModuleType("learnerbot")
    package.__path__ = [str(_LEARNERBOT_DIR)]
    package.__package__ = "learnerbot"
    package.__file__ = str(_LEARNERBOT_DIR / "__init__.py")
    sys.modules["learnerbot"] = package

from learnerbot import ai_council_http_patch as _http
from learnerbot import grok_provider as _grok  # noqa: F401  # installs Grok on the shared provider hook
from learnerbot import kimi_provider as _kimi  # noqa: F401  # layers Kimi on the shared provider hook


def _call_claude_without_deprecated_temperature(prompt: str) -> tuple[int, str, str]:
    """Call Claude through the existing bounded HTTP provider without temperature.

    Newer Anthropic models reject the legacy ``temperature`` field. Keep model
    discovery, credentials, timeouts, redaction and response parsing exactly on
    the repository's existing provider path; only omit that deprecated option.
    """
    env = _http._runtime_env()
    key = str(env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return 90, "", "ANTHROPIC_API_KEY missing from SiBot runtime"
    model, discovery_error = _http._discover_anthropic_model(key, env)
    if not model:
        return 92, "", discovery_error or "No Anthropic model available"

    status, body, raw, _ = _http._http_json(
        "https://api.anthropic.com/v1/messages",
        headers=_http._anthropic_headers(key),
        payload={
            "model": model,
            "max_tokens": 2400,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    text = _http._anthropic_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _http._error_detail(status, body, raw, env)


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    provider = str(provider or "").strip().lower()
    if provider == "claude":
        return _call_claude_without_deprecated_temperature(prompt)
    # Keep the event-driven bus on the shared public provider hook. Importing
    # grok_provider and kimi_provider above extends that hook to Grok and Kimi,
    # while ai_cost_provider_patch may later replace the same hook with the
    # authoritative budget gate. Looking it up dynamically here preserves both
    # providers without bypassing cost controls.
    return _http.call_provider(provider, prompt)


def install() -> None:
    """Install provider compatibility only inside the current AI-bus process."""
    from scripts import ai_agent_bus

    ai_agent_bus.AGENTS = tuple(dict.fromkeys((*ai_agent_bus.AGENTS, "grok", "kimi")))
    ai_agent_bus._AGENT_SET = set(ai_agent_bus.AGENTS)
    ai_agent_bus._SECRET_ENV_KEYS = tuple(
        dict.fromkeys(
            (*ai_agent_bus._SECRET_ENV_KEYS, "XAI_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY")
        )
    )
    if not any(getattr(pattern, "pattern", "").startswith("xai-") for pattern in ai_agent_bus._SECRET_PATTERNS):
        ai_agent_bus._SECRET_PATTERNS = (*ai_agent_bus._SECRET_PATTERNS, re.compile(r"xai-[A-Za-z0-9_-]{12,}"))

    base_prompt = ai_agent_bus._prompt

    def prompt_with_extended_agents(**kwargs):
        return base_prompt(**kwargs).replace(
            "<GPT|CLAUDE|GEMINI|DEEPSEEK|COPILOT>",
            "<GPT|CLAUDE|GEMINI|DEEPSEEK|COPILOT|GROK|KIMI>",
        )

    ai_agent_bus._prompt = prompt_with_extended_agents
    ai_agent_bus.call_provider = call_provider
