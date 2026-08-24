from __future__ import annotations

import time
import urllib.parse
from typing import Any

from . import ai_council_http_patch as _base


_DEEPSEEK_ALIAS = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
}
_DEEPSEEK_CURRENT = ("deepseek-v4-flash", "deepseek-v4-pro")


def _call_gemini_current(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    """Gemini 3.x generateContent call without removed sampling parameters."""
    key = str(env.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return 90, "", "GEMINI_API_KEY missing from SiBot runtime"
    model = str(
        env.get("GEMINI_COUNCIL_MODEL")
        or env.get("GEMINI_MASTER_MODEL")
        or env.get("GEMINI_STRATEGY_MODEL")
        or "gemini-3.7-flash"
    ).strip()
    safe_model = urllib.parse.quote(model, safe="-._")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + safe_model
        + ":generateContent?key="
        + urllib.parse.quote(key, safe="")
    )
    last: tuple[int, Any, str, dict[str, str]] = (0, None, "", {})
    for attempt in range(_base._GEMINI_MAX_ATTEMPTS):
        last = _base._http_json(
            url,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                # Gemini 3.x removed legacy sampling parameters such as
                # temperature/top_p/top_k. Keep only the output bound here.
                "generationConfig": {"maxOutputTokens": 2400},
            },
        )
        status, body, raw, headers = last
        text = _base._gemini_text(body)
        if 200 <= status < 300 and text:
            return 0, text, ""
        if status != 429 or attempt + 1 >= _base._GEMINI_MAX_ATTEMPTS:
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
