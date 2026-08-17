from __future__ import annotations

from . import telegram as _tg

_PREV_JSON = _tg._json
_MOBILE_DIVIDER = "━━━━━━━━━━━━"
_DIVIDER_CHARS = frozenset("━─—═_")


def _mobile_text(text):
    if not isinstance(text, str) or not text:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped) > len(_MOBILE_DIVIDER) and stripped and set(stripped) <= _DIVIDER_CHARS:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + _MOBILE_DIVIDER)
        else:
            out.append(line)
    return "\n".join(out)


def _json(method: str, token: str, *, payload=None, params=None, timeout=20):
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        payload = dict(payload)
        payload["text"] = _mobile_text(payload["text"])
    return _PREV_JSON(method, token, payload=payload, params=params, timeout=timeout)


def install():
    if getattr(_tg, "_mobile_divider_patch_installed", False):
        return
    _tg._json = _json
    _tg._mobile_divider_patch_installed = True


install()
