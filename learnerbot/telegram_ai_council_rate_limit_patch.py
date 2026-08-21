from __future__ import annotations

import html
import time

from . import telegram_ai_council_patch as _cui

_PREV_START_QUESTION = _cui._start_question
_LAST_USER_START: dict[str, float] = {}
_USER_COOLDOWN_SECONDS = 60


def _send_chunks(app, tid, title: str, body: str, keyboard=None) -> None:
    """Telegram-safe chunking: split raw text first, then HTML-escape each chunk."""
    raw = str(body or "").strip() or "(no answer returned)"
    limit = 3200
    chunks = [raw[i : i + limit] for i in range(0, len(raw), limit)] or [raw]
    for idx, chunk in enumerate(chunks):
        suffix = f" <i>({idx + 1}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        text = f"<b>{html.escape(title)}</b>{suffix}\n\n{html.escape(chunk)}"
        _cui._ui._send(app, tid, text, keyboard if idx == len(chunks) - 1 else None)


def _start_question(app, tid, question: str) -> None:
    tid_key = str(tid)
    master_mode = _cui._master(app, tid)
    if not master_mode:
        now = time.monotonic()
        with _cui._LOCK:
            question_running = any(owner == tid_key and ":" not in job for owner, job in _cui._INFLIGHT)
            if question_running:
                raise RuntimeError("You already have an Ask SiBot request running. Please let it finish before starting another.")
            last = float(_LAST_USER_START.get(tid_key) or 0.0)
            remaining = int(_USER_COOLDOWN_SECONDS - (now - last))
            if last and remaining > 0:
                raise RuntimeError(f"Ask SiBot is rate-limited to protect AI credit. Try again in about {remaining} seconds.")
        _PREV_START_QUESTION(app, tid, question)
        with _cui._LOCK:
            _LAST_USER_START[tid_key] = now
        return
    _PREV_START_QUESTION(app, tid, question)


def install() -> None:
    if getattr(_cui, "_ai_council_rate_limit_installed", False):
        return
    _cui._send_chunks = _send_chunks
    _cui._start_question = _start_question
    _cui._ai_council_rate_limit_installed = True


install()
