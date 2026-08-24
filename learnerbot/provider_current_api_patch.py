from __future__ import annotations

import time
from typing import Any

from . import ai_council_http_patch as _base


_DEEPSEEK_ALIAS = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
}
_DEEPSEEK_CURRENT = ("deepseek-v4-flash", "deepseek-v4-pro")
_GEMINI_PREFERRED = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
)


def _normalise_gemini_id(value: str) -> str:
    value = str(value or "").strip()
    return value[7:] if value.startswith("models/") else value


def _discover_gemini_model_current(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = _normalise_gemini_id(
        env.get("GEMINI_COUNCIL_MODEL")
        or env.get("GEMINI_MASTER_MODEL")
        or env.get("GEMINI_STRATEGY_MODEL")
        or "gemini-3.5-flash-lite"
    )
    status, body, raw, _ = _base._http_json(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key, "Accept": "application/json"},
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        # A concrete configured model can still be tried if catalogue lookup is
        # temporarily unavailable. This keeps a metadata outage from disabling
        # otherwise valid inference while preserving the provider's own error.
        return (requested, "") if requested else ("", _base._error_detail(status, body, raw, env))

    ids: list[str] = []
    for row in body.get("models") or []:
        if not isinstance(row, dict):
            continue
        methods = {str(x) for x in (row.get("supportedGenerationMethods") or [])}
        if methods and "generateContent" not in methods:
            continue
        model_id = _normalise_gemini_id(str(row.get("name") or ""))
        if model_id:
            ids.append(model_id)

    if requested in ids:
        return requested, ""
    for preferred in _GEMINI_PREFERRED:
        if preferred in ids:
            return preferred, ""
    for model_id in ids:
        if "flash-lite" in model_id.lower():
            return model_id, ""
    for model_id in ids:
        if "flash" in model_id.lower():
            return model_id, ""
    return (ids[0], "") if ids else ("", "Gemini returned no generateContent-capable models")


def _call_gemini_current(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    """Gemini generateContent call with current-model discovery and bounded retries."""
    key = str(env.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return 90, "", "GEMINI_API_KEY missing from SiBot runtime"
    model, discovery_error = _discover_gemini_model_current(key, env)
    if not model:
        return 92, "", discovery_error or "No Gemini model available"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last: tuple[int, Any, str, dict[str, str]] = (0, None, "", {})
    for attempt in range(_base._GEMINI_MAX_ATTEMPTS):
        last = _base._http_json(
            url,
            headers={
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            },
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                # Current Gemini models accept a simple output bound. Avoid
                # carrying legacy sampling options across model generations.
                "generationConfig": {"maxOutputTokens": 2400},
            },
        )
        status, body, raw, headers = last
        text = _base._gemini_text(body)
        if 200 <= status < 300 and text:
            return 0, text, ""
        if status not in {429, 500, 502, 503, 504} or attempt + 1 >= _base._GEMINI_MAX_ATTEMPTS:
            return status or 92, "", _base._error_detail(status, body, raw, env)
        time.sleep(_base._retry_delay(headers, attempt))
    status, body, raw, _ = last
    return status or 92, "", _base._error_detail(status, body, raw, env)


def _discover_deepseek_model_current(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = str(
        env.get("DEEPSEEK_COUNCIL_MODEL")
        or env.get("DEEPSEEK_MASTER_MODEL")
        or "deepseek-v4-flash"
    ).strip()
    requested = _DEEPSEEK_ALIAS.get(requested.lower(), requested)
    status, body, raw, _ = _base._http_json(
        "https://api.deepseek.com/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        # A transient model-list failure must not fall back to API model aliases
        # retired on 24 July 2026.
        if requested in _DEEPSEEK_CURRENT:
            return requested, ""
        return "", _base._error_detail(status, body, raw, env)

    ids = [
        str(row.get("id") or "")
        for row in (body.get("data") or [])
        if isinstance(row, dict) and str(row.get("id") or "")
    ]
    if requested in ids:
        return requested, ""
    for preferred in _DEEPSEEK_CURRENT:
        if preferred in ids:
            return preferred, ""
    return (ids[0], "") if ids else ("", "DeepSeek returned no available models")


def install() -> None:
    if getattr(_base, "_current_api_compat_installed", False):
        return
    _base._call_gemini = _call_gemini_current
    _base._discover_deepseek_model = _discover_deepseek_model_current
    _base._current_api_compat_installed = True


install()
