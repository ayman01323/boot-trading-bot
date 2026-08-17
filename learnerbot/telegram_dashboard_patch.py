from __future__ import annotations

import html
import threading

from . import telegram_ui as _ui
from .capital_dashboard import master_dashboard_text,user_dashboard_text

_original_menu_keyboard=_ui.menu_keyboard
_original_handle_update=_ui.handle_update
_REFRESHING=set()
_REFRESH_LOCK=threading.Lock()


def menu_keyboard(app=None,chat_id=None):
    kb=_original_menu_keyboard(app,chat_id);rows=list(kb.get("inline_keyboard") or [])
    if app is not None and chat_id is not None:
        if _ui._master(app,chat_id):
            rows.insert(1,[{"text":"🏦 Trading Wallets & Capital","callback_data":"menu:adminwallets"},{"text":"📊 My Capital & P&L","callback_data":"menu:capital"}])
        else:
            rows.insert(0,[{"text":"📊 My Capital & P&L","callback_data":"menu:capital"}])
    else:
        rows.insert(0,[{"text":"📊 My Capital & P&L","callback_data":"menu:capital"}])
    return {"inline_keyboard":rows}


def _message_context(update):
    m=update.get("message") or update.get("edited_message")
    if not m:return None,""
    return (m.get("chat") or {}).get("id"),str(m.get("text") or "").strip()


def _send_error(app,chat_id,exc):
    _ui._send(app,chat_id,f"❌ Dashboard refresh failed: <code>{html.escape(type(exc).__name__)}</code> — {html.escape(str(exc)[:300])}",_ui.back_keyboard())


def _refresh_key(chat_id,master):
    return (str(chat_id),bool(master))


def _dashboard_worker(app,chat_id,master,key):
    try:
        out=master_dashboard_text(app,chat_id) if master else user_dashboard_text(app,chat_id)
        _ui._send(app,chat_id,out,_ui.back_keyboard())
    except Exception as exc:
        _send_error(app,chat_id,exc)
    finally:
        with _REFRESH_LOCK:_REFRESHING.discard(key)


def _start_dashboard_refresh(app,chat_id,master=False):
    key=_refresh_key(chat_id,master)
    with _REFRESH_LOCK:
        if key in _REFRESHING:return False
        _REFRESHING.add(key)
    try:
        _ui._send(app,chat_id,"⏳ Refreshing live wallet balances and accounting. You can continue using the menu while this runs.")
        t=threading.Thread(target=_dashboard_worker,args=(app,chat_id,master,key),daemon=True,name=f"telegram-capital-{chat_id}")
        t.start();return True
    except Exception:
        with _REFRESH_LOCK:_REFRESHING.discard(key)
        raise


def handle_update(app,update):
    cb=update.get("callback_query")
    if cb:
        chat_id=((cb.get("message") or {}).get("chat") or {}).get("id");data=str(cb.get("data") or "");cqid=cb.get("id")
        if data in {"menu:capital","menu:adminwallets"}:
            if not _ui._auth(app,chat_id):
                if cqid:_ui.answer_callback_query(app.telegram_bot_token,cqid,"Not authorised.")
                return
            master=data=="menu:adminwallets"
            if master and not _ui._master(app,chat_id):
                if cqid:_ui.answer_callback_query(app.telegram_bot_token,cqid,"MASTER only")
                return
            key=_refresh_key(chat_id,master)
            with _REFRESH_LOCK:busy=key in _REFRESHING
            if cqid:_ui.answer_callback_query(app.telegram_bot_token,cqid,"Already refreshing…" if busy else "Refreshing live balances…")
            if not busy:
                try:_start_dashboard_refresh(app,chat_id,master)
                except Exception as exc:_send_error(app,chat_id,exc)
            return
    chat_id,text=_message_context(update)
    if chat_id is not None and text.startswith("/"):
        cmd=text.split(maxsplit=1)[0].split("@",1)[0].lower()
        if cmd in {"/capital","/adminwallets"}:
            if not _ui._auth(app,chat_id):
                _ui._send(app,chat_id,"Not authorised. Use <code>/join</code> or activate your account.");return
            master=cmd=="/adminwallets"
            if master and not _ui._master(app,chat_id):
                _ui._send(app,chat_id,"MASTER only.",_ui.back_keyboard());return
            key=_refresh_key(chat_id,master)
            with _REFRESH_LOCK:busy=key in _REFRESHING
            if busy:_ui._send(app,chat_id,"⏳ That dashboard is already refreshing. Please wait for the current result.",_ui.back_keyboard())
            else:
                try:_start_dashboard_refresh(app,chat_id,master)
                except Exception as exc:_send_error(app,chat_id,exc)
            return
    return _original_handle_update(app,update)


def install():
    if getattr(_ui,"_capital_dashboard_patch_installed",False):return
    _ui.menu_keyboard=menu_keyboard;_ui.handle_update=handle_update;_ui._capital_dashboard_patch_installed=True

install()
