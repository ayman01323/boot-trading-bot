from __future__ import annotations

import os
from typing import Any

from . import ai_council as _council
from . import ai_council_http_patch as _http

_PROVIDER = "grok"
_DEFAULT_MODEL = "grok-4.20-non-reasoning"
_BASE_HTTP_CALL = _http.call_provider

# Let the existing runtime credential bridge load/redact the xAI key exactly like
# the other provider secrets. The key is never persisted in Council session data.
_http._SECRET_KEYS.add("XAI_API_KEY")


def _xai_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n".join(x.strip() for x in chunks if x.strip()).strip()
    return ""


def call_grok(prompt: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Call xAI through its OpenAI-compatible REST API in advisory text-only mode."""
    env = dict(env or _http._runtime_env())
    key = str(env.get("XAI_API_KEY") or "").strip()
    if not key:
        return 90, "", "XAI_API_KEY missing from SiBot runtime"

    model = str(
        env.get("XAI_COUNCIL_MODEL")
        or env.get("GROK_COUNCIL_MODEL")
        or env.get("XAI_MASTER_MODEL")
        or _DEFAULT_MODEL
    ).strip()

    status, body, raw, _ = _http._http_json(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        payload={
            "model": model,
            "messages": [{"role": "user", "content": str(prompt or "")}],
            "temperature": 0.2,
            "max_tokens": 2400,
        },
    )
    text = _xai_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _http._error_detail(status, body, raw, env)


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    provider = str(provider or "").strip().lower()
    if provider == _PROVIDER:
        return call_grok(prompt)
    return _BASE_HTTP_CALL(provider, prompt)


def install() -> None:
    if getattr(_council, "_grok_provider_installed", False):
        return
    providers = tuple(dict.fromkeys((*_council.PROVIDERS, _PROVIDER)))
    _council.PROVIDERS = providers
    _council.LEADERS = providers
    _http.call_provider = call_provider
    _council.call_provider = call_provider
    _council._grok_provider_installed = True


install()
