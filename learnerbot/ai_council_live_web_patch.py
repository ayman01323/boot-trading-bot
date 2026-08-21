from __future__ import annotations

import re

from . import ai_council_http_patch as _http

_BASE_OPENAI = _http._call_openai

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


def _needs_web_search(prompt: str) -> bool:
    """Use paid live search only for the final PasPuss answer when freshness matters."""
    if not _is_final_paspuss_prompt(prompt):
        return False
    question = _user_question(prompt).lower()
    return any(re.search(pattern, question, flags=re.IGNORECASE) for pattern in _FRESH_PATTERNS)


def _web_models(env: dict[str, str]) -> list[str]:
    configured = str(env.get("OPENAI_COUNCIL_MODEL") or "gpt-5.6-terra").strip()
    fallback = str(env.get("OPENAI_WEB_MODEL") or "gpt-5.4").strip()
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
        return _BASE_OPENAI(prompt, env)

    live_prompt = str(prompt or "") + """

LIVE INFORMATION RULE:
This question depends on fresh public information. Use the web search tool before answering. Base time-sensitive claims on the search results. Answer directly as PasPuss AI. Do not tell the user that you lack internet access when the search succeeds, and do not mention the internal search/tool process.
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
                "tools": [{"type": "web_search_preview", "search_context_size": "medium"}],
                "tool_choice": "required",
            },
        )
        text = _http._openai_text(body)
        if 200 <= status < 300 and text:
            return 0, text, ""
        last_status, last_body, last_raw = status, body, raw
        # Retry with the documented web model only for request/model/tool compatibility
        # errors. Quota/auth/network/server failures should not be multiplied.
        if status not in {400, 404, 422}:
            break

    # Preserve a useful, honest PasPuss response if the hosted search service itself
    # is temporarily unavailable. Never invent a current value.
    fallback_prompt = str(prompt or "") + """

LIVE INFORMATION FALLBACK:
A live lookup could not be completed for this request. Do not invent a current value and do not claim PasPuss AI never has live-data capability. Say only that the live information could not be retrieved at this moment, then give any non-time-sensitive help that remains useful.
"""
    rc, out, err = _BASE_OPENAI(fallback_prompt, env)
    if rc == 0 and out:
        return rc, out, err
    return last_status or rc or 92, "", _http._error_detail(last_status, last_body, last_raw, env)


def install() -> None:
    if getattr(_http, "_paspuss_live_web_patch_installed", False):
        return
    _http._call_openai = _call_openai
    _http._paspuss_live_web_patch_installed = True


install()
