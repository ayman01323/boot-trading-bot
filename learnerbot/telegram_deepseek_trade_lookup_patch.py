from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

from . import telegram_ai_ops_patch as _ai
from . import telegram_ai_reports_menu_patch as _menu
from . import telegram_deepseek_control_patch as _ds
from . import telegram_ui as _ui
from .ai_ops_status import read_json

_PREV_HANDLE = _ui.handle_update
_PREV_KEYBOARD = _ds._keyboard
_PREV_TEXT = _ds._text
_PENDING_EXACT: dict[str, float] = {}
REQUEST_TTL_SECONDS = 600
REQUEST_PATH = Path("/var/tmp/boot/deepseek_trade_lookup_request.json")
EXACT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o644)


def _next_nonce() -> int:
    try:
        current = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
        return max(0, int(current.get("request_nonce") or 0)) + 1
    except Exception:
        return 1


def _queue_request(lookup_type: str, identifier: str, requested_by) -> dict:
    lookup_type = str(lookup_type or "").strip().lower()
    identifier = str(identifier or "").strip()
    if lookup_type == "account":
        if not identifier.isdigit() or len(identifier) > 20:
            raise ValueError("invalid Telegram account ID")
    elif lookup_type == "exact":
        if not EXACT_RE.fullmatch(identifier):
            raise ValueError("invalid position/trade/event identifier")
    else:
        raise ValueError("unsupported trade lookup type")
    value = {
        "schema_version": 1,
        "lookup_type": lookup_type,
        "identifier": identifier,
        "request_nonce": _next_nonce(),
        "requested_epoch": int(time.time()),
        "requested_by": str(requested_by or "")[:80],
    }
    _atomic_json(REQUEST_PATH, value)
    return value


def _keyboard() -> dict:
    kb = _PREV_KEYBOARD()
    rows = list(kb.get("inline_keyboard") or [])
    trade_row = [
        {"text": "🔍 My latest trade", "callback_data": "dslookup:account"},
        {"text": "🔎 Exact trade/position", "callback_data": "dslookup:exact"},
    ]
    if not any(any(str(b.get("callback_data") or "").startswith("dslookup:") for b in row) for row in rows):
        insert_at = max(0, len(rows) - 1)
        rows.insert(insert_at, trade_row)
    return {"inline_keyboard": rows}


def _trade_result_text() -> str:
    root = _ai._repo_root()
    result = read_json(root, "trades/deepseek/latest.json") or {}
    if not isinstance(result, dict) or not result:
        return ""
    lookup = result.get("lookup") or {}
    status = str(result.get("status") or "UNKNOWN").upper()
    identifier = str(lookup.get("identifier") or "")
    count = int(lookup.get("count") or 0)
    analysis = str(result.get("deepseek_analysis") or "").strip()
    lines = [
        "",
        "<b>🔍 Latest bounded trade lookup</b>",
        f"Status: <b>{html.escape(status)}</b>",
        f"Identifier: <code>{html.escape(identifier[:160])}</code>",
        f"Records: <b>{count}</b>",
    ]
    if analysis:
        lines += ["", html.escape(analysis[:1200])]
    return "\n".join(lines)


def _text(app) -> str:
    return _PREV_TEXT(app) + _trade_result_text()


def _handle_pending(app, message: dict) -> bool:
    tid = (message.get("chat") or {}).get("id")
    if tid is None or str(tid) not in _PENDING_EXACT:
        return False
    if not _menu._is_master(app, tid):
        _PENDING_EXACT.pop(str(tid), None)
        return False
    text = str(message.get("text") or "").strip()
    if text.startswith("/"):
        return False
    if time.time() > _PENDING_EXACT.get(str(tid), 0):
        _PENDING_EXACT.pop(str(tid), None)
        _ui._send(app, tid, "⌛ Trade lookup request expired. Open DeepSeek Control and try again.")
        return True
    if text.lower() in {"cancel", "cancel."}:
        _PENDING_EXACT.pop(str(tid), None)
        _ui._send(app, tid, "✅ Trade lookup cancelled.")
        return True
    if not EXACT_RE.fullmatch(text):
        _ui._send(
            app,
            tid,
            "❌ Invalid identifier. Use only letters, numbers, <code>_ . : -</code> (maximum 128 characters), or send <code>cancel</code>.",
        )
        return True
    req = _queue_request("exact", text, tid)
    _PENDING_EXACT.pop(str(tid), None)
    _ui._send(
        app,
        tid,
        f"✅ 🔴 DeepSeek exact trade lookup queued as request #{req['request_nonce']}.\n"
        "The bounded GitHub bridge will process it within about 5 minutes.",
        _keyboard(),
    )
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    if message and _handle_pending(app, message):
        return

    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data.startswith("dslookup:"):
            if not _menu._is_master(app, tid):
                _menu._answer(app, cb, "MASTER only")
                return
            if data == "dslookup:account":
                req = _queue_request("account", str(tid), tid)
                _menu._answer(app, cb, f"Trade lookup queued #{req['request_nonce']}")
                _ui._send(
                    app,
                    tid,
                    f"✅ 🔴 DeepSeek will inspect the latest bounded trading records for Telegram ID <code>{html.escape(str(tid))}</code>.\n"
                    "The GitHub bridge will process the request within about 5 minutes.",
                    _keyboard(),
                )
                return
            if data == "dslookup:exact":
                _PENDING_EXACT[str(tid)] = time.time() + REQUEST_TTL_SECONDS
                _menu._answer(app, cb, "Send position/trade/event ID")
                _ui._send(
                    app,
                    tid,
                    "<b>🔎 Exact trade/position lookup</b>\n\n"
                    "Send the position ID, provenance event ID, trade ID or transaction/signature identifier.\n"
                    "No paths, shell commands or SQL are accepted. Send <code>cancel</code> to stop.",
                )
                return

    return _PREV_HANDLE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_deepseek_trade_lookup_patch_installed", False):
        return
    _ds._keyboard = _keyboard
    _ds._text = _text
    _ui.handle_update = handle_update
    _ui._telegram_deepseek_trade_lookup_patch_installed = True


install()
