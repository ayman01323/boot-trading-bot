from __future__ import annotations

from typing import Any

from . import ai_council as _council
from . import ai_council_http_patch as _http

_PROVIDER = "kimi"
_DEFAULT_MODEL = "kimi-k2.6"
_DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
_BASE_HTTP_CALL = _http.call_provider

# Accept both Kimi's newer naming and Moonshot's documented platform naming.
# Runtime secret fallback reads this set and never logs secret values.
_http._SECRET_KEYS.add("KIMI_API_KEY")
_http._SECRET_KEYS.add("MOONSHOT_API_KEY")


def _kimi_text(body: Any) -> str:
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


def _base_url(env: dict[str, str]) -> str:
    return str(
        env.get("KIMI_BASE_URL")
        or env.get("MOONSHOT_BASE_URL")
        or _DEFAULT_BASE_URL
    ).strip().rstrip("/")


def _configured_model(env: dict[str, str]) -> str:
    return str(
        env.get("KIMI_COUNCIL_MODEL")
        or env.get("MOONSHOT_COUNCIL_MODEL")
        or env.get("KIMI_MASTER_MODEL")
        or _DEFAULT_MODEL
    ).strip()


def _discover_kimi_model(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = _configured_model(env)
    status, body, raw, _ = _http._http_json(
        f"{_base_url(env)}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        # Keep an explicitly configured/default model usable when the catalogue
        # endpoint is transiently unavailable; the inference call will then
        # return the provider's precise model error if it is genuinely stale.
        return (requested, "") if requested else ("", _http._error_detail(status, body, raw, env))

    ids = [
        str(row.get("id") or "")
        for row in (body.get("data") or [])
        if isinstance(row, dict) and str(row.get("id") or "")
    ]
    if requested in ids:
        return requested, ""
    for model_id in ids:
        if model_id.lower().startswith("kimi-k2.6"):
            return model_id, ""
    for model_id in ids:
        if model_id.lower().startswith("kimi-k2"):
            return model_id, ""
    for model_id in ids:
        if "kimi" in model_id.lower():
            return model_id, ""
    return "", "Moonshot returned no Kimi chat model"


def call_kimi(prompt: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Call Kimi through Moonshot's OpenAI-compatible API in advisory text mode."""
    env = dict(_http._runtime_env() if env is None else env)
    key = str(env.get("KIMI_API_KEY") or env.get("MOONSHOT_API_KEY") or "").strip()
    if not key:
        return 90, "", "KIMI_API_KEY or MOONSHOT_API_KEY missing from SiBot runtime"

    model, discovery_error = _discover_kimi_model(key, env)
    if not model:
        return 92, "", discovery_error or "No Kimi model available"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": str(prompt or "")}],
        "max_tokens": 2400,
    }
    # K2.6/K2.5 support an explicit thinking switch. Routine bus traffic defaults
    # to disabled reasoning to minimise latency/cost; operators can opt in.
    if model.startswith(("kimi-k2.6", "kimi-k2.5")):
        thinking = str(env.get("KIMI_THINKING") or "disabled").strip().lower()
        payload["thinking"] = {"type": "enabled" if thinking in {"1", "true", "yes", "on", "enabled"} else "disabled"}

    status, body, raw, _ = _http._http_json(
        f"{_base_url(env)}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    text = _kimi_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _http._error_detail(status, body, raw, env)


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    provider = str(provider or "").strip().lower()
    if provider == _PROVIDER:
        return call_kimi(prompt)
    return _BASE_HTTP_CALL(provider, prompt)


def install() -> None:
    if getattr(_council, "_kimi_provider_installed", False):
        return
    providers = tuple(dict.fromkeys((*_council.PROVIDERS, _PROVIDER)))
    _council.PROVIDERS = providers
    _council.LEADERS = providers
    _http.call_provider = call_provider
    _council.call_provider = call_provider
    _council._kimi_provider_installed = True


install()
