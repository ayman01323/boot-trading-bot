from __future__ import annotations

import re

from . import ai_council_http_patch as _http

_BASE_OPENAI = _http._call_openai

LIVE_UNAVAILABLE_TEXT = (
    "I couldn’t retrieve the live information just now. Please try again shortly."
)

_FRESH_PATTERNS = (
    r"\bcurrent(?:ly)?\b",
    r"\bright now\b",
    r"\btoday\b",
    r"\btonight\b",
    r"\btomorrow\b",
    r"\bthis (?:morning|afternoon|evening|week|month|year)\b",
    r"\blatest\b",
    r"\blive\b",
    r"\brecent\b",
    r"\bweather\b",
    r"\btemperature\b",
    r"\bforecast\b",
    r"\bnews\b",
    r"\bprice\b",
    r"\bexchange rate\b",
    r"\bmarket\b",
    r"\bscore\b",
    r"\bresult\b",
    r"\boutage\b",
    r"\bdelay(?:ed|s)?\b",
    r"\bopen now\b",
    r"\bavailable now\b",
)

_OFFLINE_REFUSAL_PATTERNS = (
    r"\b(?:i|we) (?:do not|don't|cannot|can't) (?:have )?access to (?:live|real[- ]?time|current)\b",
    r"\b(?:i|we) cannot provide (?:the )?(?:current|real[- ]?time|live)\b",
    r"\bcheck (?:a )?live service\b",
    r"\bcheck .*?(?:met office|bbc weather|weather app)\b",
    r"\breal[- ]?time .*? require(?:s)? access to current external apis\b",
)


def _user_question(prompt: str) -> str:
    text = str(prompt or "")
    match = re.search(
        r"USER QUESTION:\s*(.*?)\n\nPRIVATE DRAFTING MATERIAL:",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return text


def _is_final_paspuss_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    return "You are PasPuss AI." in text and "USER QUESTION:" in text


def _question_requires_live(question: str) -> bool:
    text = str(question or "").lower()
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _FRESH_PATTERNS)


def _needs_web_search(prompt: str) -> bool:
    """Use paid live search only for the final PasPuss answer when freshness matters."""
    return _is_final_paspuss_prompt(prompt) and _question_requires_live(_user_question(prompt))


def _looks_like_offline_refusal(text: str) -> bool:
    raw = str(text or "").lower()
    return any(re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL) for pattern in _OFFLINE_REFUSAL_PATTERNS)


def _web_models(env: dict[str, str]) -> list[str]:
    configured = str(env.get("OPENAI_COUNCIL_MODEL") or "gpt-5.6-terra").strip()
    fallback = str(env.get("OPENAI_WEB_MODEL") or "gpt-5.6").strip()
    out: list[str] = []
    for model in (configured, fallback):
        if model and model not in out:
            out.append(model)
    return out


def _call_openai(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    if not _needs_web_search(prompt):
        return _BASE_OPENAI(prompt, env)

    key = str(env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or "").strip()
    if not key:
        return 0, LIVE_UNAVAILABLE_TEXT, ""

    live_prompt = str(prompt or "") + """

LIVE INFORMATION RULE:
This question depends on fresh public information. You MUST use the web search tool before answering. Base every time-sensitive claim on the search results. Answer directly as PasPuss AI. Do not say that PasPuss lacks live-data or internet access. Do not mention the internal search/tool process.
"""

    last_status = 0
    last_body = None
    last_raw = ""
    for model in _web_models(env):
        status, body, raw, _headers = _http._http_json(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": model,
                "input": live_prompt,
                "max_output_tokens": 2400,
                "tools": [{"type": "web_search"}],
                "tool_choice": "required",
            },
        )
        text = _http._openai_text(body)
        if 200 <= status < 300 and text and not _looks_like_offline_refusal(text):
            return 0, text, ""
        last_status, last_body, last_raw = status, body, raw
        # Retry a known current web-capable model only for request/model/tool
        # compatibility errors. Do not multiply quota/auth/network failures.
        if status not in {400, 404, 422}:
            break

    # Freshness is a hard contract. Never substitute an offline model draft for a
    # current fact because that can produce a confident but stale answer.
    _ = (last_status, last_body, last_raw)
    return 0, LIVE_UNAVAILABLE_TEXT, ""


def install() -> None:
    if getattr(_http, "_paspuss_live_web_patch_installed", False):
        return
    _http._call_openai = _call_openai
    _http._paspuss_live_web_patch_installed = True


install()
