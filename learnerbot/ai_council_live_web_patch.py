from __future__ import annotations

import re
import urllib.parse

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


def _live_prompt(prompt: str) -> str:
    return str(prompt or "") + """

LIVE INFORMATION RULE:
This question depends on fresh public information. You MUST use live web search before answering. Base every time-sensitive claim on the retrieved live sources. Answer directly as PasPuss AI. Do not say that PasPuss lacks live-data or internet access. Do not mention the internal search/tool process or the underlying provider.
"""


def _gemini_is_grounded(body) -> bool:
    if not isinstance(body, dict):
        return False
    for cand in body.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        meta = cand.get("groundingMetadata") or {}
        if isinstance(meta, dict) and (meta.get("webSearchQueries") or meta.get("groundingChunks")):
            return True
    return False


def _call_gemini_grounded(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    key = str(env.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return 90, "", "GEMINI_API_KEY missing"
    model = str(
        env.get("GEMINI_COUNCIL_MODEL")
        or env.get("GEMINI_MASTER_MODEL")
        or env.get("GEMINI_STRATEGY_MODEL")
        or "gemini-3.7-flash"
    ).strip()
    safe_model = urllib.parse.quote(model, safe="-._")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
    status, body, raw, _headers = _http._http_json(
        url,
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        },
        payload={
            "contents": [{"parts": [{"text": _live_prompt(prompt)}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2400},
        },
    )
    text = _http._gemini_text(body)
    if 200 <= status < 300 and text and _gemini_is_grounded(body) and not _looks_like_offline_refusal(text):
        return 0, text, ""
    return status or 92, "", _http._error_detail(status, body, raw, env)


def _call_openai(prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
    if not _needs_web_search(prompt):
        return _BASE_OPENAI(prompt, env)

    key = str(env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or "").strip()
    if key:
        live_prompt = _live_prompt(prompt)
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
            # Retry a known current web-capable OpenAI model only for request/model/tool
            # compatibility errors. Quota/auth/network failures move to the independent
            # grounded Gemini fallback rather than multiplying OpenAI failures.
            if status not in {400, 404, 422}:
                break

    # OpenAI web search can be rate-limited independently of ordinary model usage.
    # Use Gemini 3.7 Flash + Google Search grounding as the live fallback. Accept it
    # only when the API confirms grounding metadata was actually produced.
    grc, gout, _gerr = _call_gemini_grounded(prompt, env)
    if grc == 0 and gout:
        return 0, gout, ""

    # Freshness is a hard contract. Never substitute an offline draft for a current
    # fact because that can produce a confident but stale or capability-refusal answer.
    return 0, LIVE_UNAVAILABLE_TEXT, ""


def install() -> None:
    if getattr(_http, "_paspuss_live_web_patch_installed", False):
        return
    _http._call_openai = _call_openai
    _http._paspuss_live_web_patch_installed = True


install()
