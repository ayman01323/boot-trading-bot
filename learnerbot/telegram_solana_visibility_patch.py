from __future__ import annotations

from . import telegram_sibot_patch as _tg
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_ui as _ui

_PREV_CHAIN_PICKER = _tg._chain_picker
_PREV_SIBOT_KEYBOARD = _tg.sibot_keyboard
_PREV_TOP20_SUMMARY = _tg.top20_summary_page
_PREV_HANDLE_UPDATE = _ui.handle_update


def _has_callback(kb: dict, callback: str) -> bool:
    return any(
        str(button.get("callback_data") or "") == callback
        for row in (kb.get("inline_keyboard") or [])
        for button in row
    )


def _ensure_solana_picker(kb: dict, prefix: str, back: str = "menu:sibot") -> dict:
    rows = [list(row) for row in (kb.get("inline_keyboard") or [])]
    callback = f"{prefix}:solana"
    # Remove any stale/duplicate Solana button and then put one in a predictable
    # first row so it cannot be hidden after a long EVM chain list.
    cleaned = []
    for row in rows:
        kept = [b for b in row if str(b.get("callback_data") or "") != callback]
        if kept:
            cleaned.append(kept)
    cleaned.insert(0, [{"text": "🟣 SOLANA", "callback_data": callback}])
    return {"inline_keyboard": cleaned}


def chain_picker(app, prefix, back="menu:sibot"):
    kb = _PREV_CHAIN_PICKER(app, prefix, back)
    if prefix in {"sibot:top20", "sibot:leaders"}:
        return _ensure_solana_picker(kb, prefix, back)
    return kb


def sibot_keyboard(app, tid):
    kb = _PREV_SIBOT_KEYBOARD(app, tid)
    if _has_callback(kb, "sibot:solana:top20"):
        return kb
    rows = [list(r) for r in (kb.get("inline_keyboard") or [])]
    # Direct access in addition to the existing Solana dashboard button.
    insert_at = 3 if len(rows) >= 3 else len(rows)
    rows.insert(insert_at, [{"text": "🟣 Solana Top 20", "callback_data": "sibot:solana:top20"}])
    return {"inline_keyboard": rows}


def top20_summary_page(app, tid):
    text = str(_PREV_TOP20_SUMMARY(app, tid))
    if "Solana" not in text:
        try:
            n = len(_intel._sol.ranking_rows(app, tid))
            text += f"\n🟣 <b>Solana</b>  •  <b>{n}</b> profitable wallet{'s' if n != 1 else ''}"
        except Exception:
            text += "\n🟣 <b>Solana</b>  •  research starting"
    return text


def handle_update(app, update):
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        parts = text.split()
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""
        if cmd in {"/sibottop20", "/sibotleaders"} and arg in {"sol", "solana"}:
            if not _ui._auth(app, tid):
                return
            if cmd == "/sibottop20":
                _ui._send(app, tid, _intel.solana_top20_page(app, tid), _intel.solana_keyboard())
            else:
                _ui._send(app, tid, _intel.solana_leaders_page(app, tid), _intel.solana_keyboard())
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_solana_visibility_patch_installed", False):
        return
    _tg._chain_picker = chain_picker
    _tg.sibot_keyboard = sibot_keyboard
    _tg.top20_summary_page = top20_summary_page
    _ui.handle_update = handle_update
    _ui._solana_visibility_patch_installed = True


install()
