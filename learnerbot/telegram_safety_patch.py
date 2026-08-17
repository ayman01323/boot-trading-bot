from __future__ import annotations

from . import telegram as _tg

if not getattr(_tg,"_boot_telegram_safety_installed",False):
    _original_json=_tg._json
    _original_answer_callback_query=_tg.answer_callback_query

    def _safe_json(method:str,token:str,*,payload=None,params=None,timeout=20):
        try:
            return _original_json(method,token,payload=payload,params=params,timeout=timeout)
        except Exception as exc:
            response=getattr(exc,"response",None)
            status=getattr(response,"status_code",None)
            detail=f"HTTP {status}" if status is not None else type(exc).__name__
            raise RuntimeError(f"Telegram API {method} failed ({detail})") from None

    def _safe_answer_callback_query(token:str,callback_query_id:str,text:str="") -> None:
        # Callback acknowledgements expire quickly. A stale/invalid acknowledgement
        # must never cancel the action requested by the user.
        try:
            _original_answer_callback_query(token,callback_query_id,text)
        except Exception:
            return None

    _tg._json=_safe_json
    _tg.answer_callback_query=_safe_answer_callback_query
    _tg._boot_telegram_safety_installed=True
