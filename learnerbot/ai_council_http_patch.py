from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from . import ai_council as _council

_BASE_CALL_PROVIDER = _council.call_provider
_REPO_ENV = Path(__file__).resolve().parents[1] / ".env"
_RUNTIME_ENV = Path(os.environ.get("AI_COUNCIL_RUNTIME_ENV") or "/var/tmp/boot/ai_council_runtime.env")
_SECRET_KEYS = {
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "COPILOT_ASSIGN_TOKEN",
}
_HTTP_TIMEOUT = 90
_GEMINI_MAX_ATTEMPTS = 4


def _runtime_env() -> dict[str, str]:
    """Build a fresh provider environment without mutating process-global secrets."""
    env = {str(k): str(v) for k, v in os.environ.items()}
    try:
        for key, value in (dotenv_values(_REPO_ENV) or {}).items():
            if value and not env.get(str(key)):
                env[str(key)] = str(value)
    except Exception:
        pass

    # GitHub Secrets synced by the self-hosted credential bridge are authoritative
    # for provider credentials only. Model/config values continue to come from the
    # normal service environment/.env.
    try:
        values = dotenv_values(_RUNTIME_ENV) or {}
        for key in _SECRET_KEYS:
            value = values.get(key)
            if value:
                env[key] = str(value)
    except Exception:
        pass
    return env


def _redact(text: str, env: dict[str, str]) -> str:
    out = str(text or "")
    for key in _SECRET_KEYS:
        secret = str(env.get(key) or "").strip()
        if secret:
            out = out.replace(secret, "[REDACTED]")
    # Never return bearer material even if a provider echoed a header fragment.
    import re
    out = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+", r"\1[REDACTED]", out)
    out = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,\"'}]+", r"\1[REDACTED]", out)
    return out[:1600]


def _http_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: int = _HTTP_TIMEOUT,
) -> tuple[int, dict[str, Any] | list[Any] | None, str, dict[str, str]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = None
            return int(resp.status or 200), body, raw, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = None
        return int(exc.code or 500), body, raw, dict(exc.headers.items()) if exc.headers else {}
    except Exception as exc:
        # Do not include the URL: Gemini carries its API key in the query string.
        return 0, None, f"{type(exc).__name__}: provider network request failed", {}


def _error_detail(status: int, body: Any, raw: str, env: dict[str, str]) -> str:
    detail = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or err.get("code") or "")
        elif err:
            detail = str(err)
        if not detail:
            detail = str(body.get("message") or body.get("detail") or "")
    if not detail:
        detail = str(raw or "")
    if status:
        detail = f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
    return _redact(detail or "provider request failed", env)


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    raw = str(headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    try:
        return max(1.0, min(float(raw), 30.0))
    except Exception:
        return min(float(2 ** (attempt + 1)) + random.uniform(0.0, 0.5), 15.0)


def _openai_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    if isinstance(body.get("output_text"), str) and body["output_text"].strip():
        return body["output_text"].strip()
    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(x.strip() for x in chunks if x.strip()).strip()


def _call_openai(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    key = str(env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or "").strip()
    if not key:
        return 90, "", "OPENAI_API_KEY missing from SiBot runtime"
    model = str(env.get("OPENAI_COUNCIL_MODEL") or "gpt-5.6-terra").strip()
    status, body, raw, _ = _http_json(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        payload={
            "model": model,
            "input": prompt,
            "max_output_tokens": 2400,
        },
    )
    text = _openai_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _error_detail(status, body, raw, env)


def _gemini_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    chunks: list[str] = []
    for cand in body.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content") or {}
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(x.strip() for x in chunks if x.strip()).strip()


def _call_gemini(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
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
    last = (0, None, "", {})
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        last = _http_json(
            url,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2400},
            },
        )
        status, body, raw, headers = last
        text = _gemini_text(body)
        if 200 <= status < 300 and text:
            return 0, text, ""
        if status != 429 or attempt + 1 >= _GEMINI_MAX_ATTEMPTS:
            return status or 92, "", _error_detail(status, body, raw, env)
        time.sleep(_retry_delay(headers, attempt))
    status, body, raw, _ = last
    return status or 92, "", _error_detail(status, body, raw, env)


def _anthropic_headers(key: str) -> dict[str, str]:
    return {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


def _discover_anthropic_model(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = str(
        env.get("ANTHROPIC_COUNCIL_MODEL")
        or env.get("CLAUDE_API_MODEL")
        or env.get("CLAUDE_MASTER_MODEL")
        or ""
    ).strip()
    if requested.startswith("claude-"):
        return requested, ""
    status, body, raw, _ = _http_json(
        "https://api.anthropic.com/v1/models?limit=100",
        headers=_anthropic_headers(key),
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        return "", _error_detail(status, body, raw, env)
    ids = [
        str(row.get("id") or "")
        for row in (body.get("data") or [])
        if isinstance(row, dict) and str(row.get("id") or "")
    ]
    if requested and requested.lower() not in {"sonnet", "claude", "auto"}:
        for model_id in ids:
            if requested.lower() in model_id.lower():
                return model_id, ""
    for model_id in ids:
        if "sonnet" in model_id.lower():
            return model_id, ""
    return (ids[0], "") if ids else ("", "Anthropic returned no available models")


def _anthropic_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    chunks: list[str] = []
    for part in body.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "\n".join(x.strip() for x in chunks if x.strip()).strip()


def _call_claude(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    key = str(env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return 90, "", "ANTHROPIC_API_KEY missing from SiBot runtime"
    model, discovery_error = _discover_anthropic_model(key, env)
    if not model:
        return 92, "", discovery_error or "No Anthropic model available"
    status, body, raw, _ = _http_json(
        "https://api.anthropic.com/v1/messages",
        headers=_anthropic_headers(key),
        payload={
            "model": model,
            "max_tokens": 2400,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    text = _anthropic_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _error_detail(status, body, raw, env)


def _discover_deepseek_model(key: str, env: dict[str, str]) -> tuple[str, str]:
    requested = str(
        env.get("DEEPSEEK_COUNCIL_MODEL")
        or env.get("DEEPSEEK_MASTER_MODEL")
        or ""
    ).strip()
    status, body, raw, _ = _http_json(
        "https://api.deepseek.com/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if not (200 <= status < 300) or not isinstance(body, dict):
        # If a concrete standard API model was configured, still try it even when
        # model discovery is temporarily unavailable.
        if requested in {"deepseek-chat", "deepseek-reasoner"}:
            return requested, ""
        return "", _error_detail(status, body, raw, env)
    ids = [
        str(row.get("id") or "")
        for row in (body.get("data") or [])
        if isinstance(row, dict) and str(row.get("id") or "")
    ]
    if requested:
        for model_id in ids:
            if model_id == requested or requested.lower() in model_id.lower():
                return model_id, ""
    for preferred in ("deepseek-chat", "deepseek-reasoner"):
        if preferred in ids:
            return preferred, ""
    for model_id in ids:
        if "chat" in model_id.lower():
            return model_id, ""
    return (ids[0], "") if ids else ("", "DeepSeek returned no available models")


def _deepseek_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    try:
        return str(body["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""


def _deepseek_max_tokens(env: dict[str, str]) -> int:
    """Bound the provider response budget without ever exposing reasoning_content."""
    raw = str(env.get("DEEPSEEK_COUNCIL_MAX_TOKENS") or "2400").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2400
    return max(512, min(value, 12000))


def _call_deepseek(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    key = str(env.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return 90, "", "DEEPSEEK_API_KEY missing from SiBot runtime"
    model, discovery_error = _discover_deepseek_model(key, env)
    if not model:
        return 92, "", discovery_error or "No DeepSeek model available"
    status, body, raw, _ = _http_json(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        payload={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": _deepseek_max_tokens(env),
        },
    )
    text = _deepseek_text(body)
    if 200 <= status < 300 and text:
        return 0, text, ""
    return status or 92, "", _error_detail(status, body, raw, env)


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    """Fast, CLI-independent provider path for interactive Telegram questions."""
    provider = str(provider or "").lower().strip()
    env = _runtime_env()
    if provider == "gpt":
        return _call_openai(prompt, env)
    if provider == "gemini":
        return _call_gemini(prompt, env)
    if provider == "claude":
        return _call_claude(prompt, env)
    if provider == "deepseek":
        return _call_deepseek(prompt, env)

    # GitHub does not expose a generic Copilot chat-completions API for this bot
    # token. Preserve the existing bounded Copilot CLI path when available. A
    # Copilot failure is isolated and never blocks GPT/other Council members.
    if provider == "copilot":
        original_env = os.environ.copy()
        try:
            os.environ.update({k: v for k, v in env.items() if k in _SECRET_KEYS and v})
            return _BASE_CALL_PROVIDER(provider, prompt)
        finally:
            for key in _SECRET_KEYS:
                if key in original_env:
                    os.environ[key] = original_env[key]
                else:
                    os.environ.pop(key, None)

    return 91, "", "unsupported provider"


def install() -> None:
    if getattr(_council, "_http_provider_patch_installed", False):
        return
    _council.call_provider = call_provider
    _council._http_provider_patch_installed = True


install()
