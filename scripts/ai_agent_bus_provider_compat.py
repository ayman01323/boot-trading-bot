from __future__ import annotations

from learnerbot import ai_council_http_patch as _http


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
    return _http.call_provider(provider, prompt)


def install() -> None:
    """Install compatibility only inside the current AI-bus process."""
    from scripts import ai_agent_bus

    ai_agent_bus.call_provider = call_provider
