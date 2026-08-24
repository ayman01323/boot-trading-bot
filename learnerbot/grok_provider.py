from __future__ import annotations

from typing import Any

from . import ai_council as _council
from . import ai_council_http_patch as _http

_PROVIDER = "grok"
_DEFAULT_MODEL = "grok-4.20-non-reasoning"
_BASE_HTTP_CALL = _http.call_provider
_GROK_ALIASES = {
    "grok-4.20-non-reasoning": "grok-4.20-0309-non-reasoning",
    "grok-4.20-non-reasoning-latest": "grok-4.20-0309-non-reasoning",
}
_GROK_PREFERRED = (
    "grok-4.20-0309-non-reasoning",
    "grok-4.6",
    "grok-4.5",
)

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


def _discover_grok_model(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = str(
        env.get("XAI_COUNCIL_MODEL")
        or env.get("GROK_COUNCIL_MODEL")
        or env.get("XAI_MASTER_MODEL")
        or _DEFAULT_MODEL
    ).strip()
    resolved = _GROK_ALIASES.get(requested.lower(), requested)
    status, body, raw, _ = _http._http_json(
        "https://api.x.ai/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        # Known current aliases/canonical IDs remain safe to try if catalogue
        # lookup is temporarily unavailable.
        if requested or resolved:
            return resolved or requested, ""
        return "", _http._error_detail(status, body, raw, env)

    ids = [
        str(row.get("id") or "")
        for row in (body.get("data") or [])
        if isinstance(row, dict) and str(row.get("id") or "")
    ]
    if requested in ids:
        return requested, ""
    if resolved in ids:
        return resolved, ""
    for preferred in _GROK_PREFERRED:
        if preferred in ids:
            return preferred, ""
    for model_id in ids:
        if model_id.startswith("grok-") and "imagine" not in model_id and "voice" not in model_id:
            return model_id, ""
    return "", "xAI returned no chat-capable Grok model"


def call_grok(prompt: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Call xAI through its OpenAI-compatible REST API in advisory text-only mode."""
    env = dict(_http._runtime_env() if env is None else env)
    key = str(env.get("XAI_API_KEY") or "").strip()
    if not key:
        return 90, "", "XAI_API_KEY missing from SiBot runtime"

    model, discovery_error = _discover_grok_model(key, env)
    if not model:
        return 92, "", discovery_error or "No Grok model available"

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
