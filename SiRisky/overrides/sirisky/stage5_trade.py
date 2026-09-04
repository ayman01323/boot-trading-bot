from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError

import requests

from .csvio import append_row, as_bool
from .jupiter import order as jup_order, execute_order, quote_only, WSOL_MINT
from .wallet import WalletStore

EXEC_HEADERS=["timestamp","order_id","action","mint","mode","status","signature","input_raw","output_raw","reason","error"]


def _compact_text(value, limit=220):
    text=" ".join(str(value or "").split())
    # Never persist URLs/query strings or obvious credentials from transport errors.
    text=re.sub(r"https?://\S+", "[url]", text, flags=re.I)
    text=re.sub(r"(?i)(api[-_]?key|x-api-key|authorization|bearer|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text[:limit]


def _json_error_message(raw):
    try:
        body=json.loads(raw)
    except Exception:
        return _compact_text(raw)
    if isinstance(body,dict):
        err=body.get("error")
        if isinstance(err,dict):
            for key in ("message","reason","code","status"):
                if err.get(key):
                    return _compact_text(err.get(key))
        if err:
            return _compact_text(err)
        for key in ("message","reason","detail","status"):
            if body.get(key):
                return _compact_text(body.get(key))
    return _compact_text(raw)


def _exception_detail(exc):
    """Extract useful detail from custom client exceptions without leaking secrets."""
    candidates=[]
    try:
        text=str(exc).strip()
        if text:
            candidates.append(text)
    except Exception:
        pass

    # Some Jupiter client exceptions keep the useful response/body on an
    # attribute while __str__ is empty or only returns the exception class.
    for attr in ("message","reason","detail","error","status","code","body","response"):
        try:
            value=getattr(exc,attr,None)
        except Exception:
            value=None
        if value is None:
            continue
        if attr=="response":
            try:
                value=getattr(value,"text",None) or getattr(value,"reason",None) or value
            except Exception:
                pass
        if isinstance(value,(dict,list,tuple)):
            try:
                value=json.dumps(value,default=str,separators=(",",":"))
            except Exception:
                value=str(value)
        text=_compact_text(value,180)
        if text:
            candidates.append(text)

    try:
        for arg in getattr(exc,"args",()) or ():
            if isinstance(arg,(dict,list,tuple)):
                try:
                    arg=json.dumps(arg,default=str,separators=(",",":"))
                except Exception:
                    arg=str(arg)
            text=_compact_text(arg,180)
            if text:
                candidates.append(text)
    except Exception:
        pass

    name=type(exc).__name__
    for text in candidates:
        compact=_compact_text(text,180)
        if compact and compact not in {name, repr(name)}:
            return compact
    return ""


def safe_execution_error(exc):
    """Return a useful but credential-safe Stage-5 error label."""
    # requests is used by the Jupiter client. Its HTTPError is a different
    # class from urllib.error.HTTPError, so handle it explicitly first.
    if isinstance(exc, requests.exceptions.HTTPError):
        response=getattr(exc,"response",None)
        code=getattr(response,"status_code",None)
        raw=""
        if response is not None:
            try:
                raw=response.text or ""
            except Exception:
                raw=""
        detail=_json_error_message(raw)
        label=f"HTTP {int(code)}" if code is not None else "HTTP_ERROR"
        return label+(f" | {detail}" if detail else "")

    if isinstance(exc, requests.exceptions.Timeout):
        return "HTTP_TIMEOUT"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "HTTP_CONNECTION_ERROR"
    if isinstance(exc, requests.exceptions.RequestException):
        return "HTTP_REQUEST_ERROR"

    if isinstance(exc,HTTPError):
        try:
            raw=exc.read().decode("utf-8",errors="replace")
        except Exception:
            raw=""
        detail=_json_error_message(raw)
        return f"HTTP {int(exc.code)}"+(f" | {detail}" if detail else "")
    if isinstance(exc,URLError):
        reason=getattr(exc,"reason",None)
        return "URL_ERROR"+(f" | {type(reason).__name__}" if reason is not None else "")

    # Preserve the sanitised payload from custom exceptions such as
    # JupiterError. Previously lower-case/punctuated messages were discarded
    # and Telegram only showed the unhelpful class name "JupiterError".
    name=type(exc).__name__
    detail=_exception_detail(exc)
    if detail:
        return _compact_text(f"{name} | {detail}",220)
    return name


class Stage5Trade:
    """Execution only. It never makes the strategy/risk decision."""
    def __init__(self, settings): self.settings=settings

    @staticmethod
    def _output_raw(q):
        for key in ("outAmount","outputAmount","estimatedOutputAmount","out_amount"):
            try:
                value=int(q.get(key) or 0)
            except Exception:
                value=0
            if value>0:
                return value
        return 0

    def execute(self, order):
        rt=self.settings.runtime(); live=as_bool(rt.get("live_enabled"),False); broadcast=as_bool(rt.get("broadcast_enabled"),False)
        manual=as_bool(rt.get("manual_approval_enabled"),False)
        external=as_bool(rt.get("manual_approval_require_external_signature"),True)

        # Defence in depth: when manual approval mode is active, Stage 5 may
        # still quote/shadow, but it must never sign/broadcast with the server
        # wallet. Final signing is intentionally external/manual.
        if live and broadcast and manual and external:
            raise RuntimeError("MANUAL_APPROVAL_EXTERNAL_SIGNATURE_REQUIRED")

        wallet=WalletStore(self.settings); taker=wallet.address(); mode="LIVE" if live and broadcast else "SHADOW"
        input_mint=WSOL_MINT if order.action=="BUY" else order.mint
        output_mint=order.mint if order.action=="BUY" else WSOL_MINT
        try:
            # SHADOW only needs an executable quote. Asking Jupiter to build a
            # transaction in paper mode adds an unnecessary failure point and
            # was causing the automatic engine loop to raise RuntimeError.
            if mode=="SHADOW":
                q=quote_only(self.settings,taker,input_mint,output_mint,order.amount_raw)
            else:
                q=jup_order(self.settings,taker,input_mint,output_mint,order.amount_raw)

            out=self._output_raw(q)
            if out<=0:
                raise RuntimeError("JUPITER_NO_EXECUTABLE_OUTPUT")

            if mode=="LIVE":
                if not wallet.has_private_key(): raise RuntimeError("SIGNER_NOT_READY")
                res=execute_order(self.settings,q,wallet.keypair_bytes()); sig=str(res.get("signature") or ""); status="SUCCESS"
                out=int(res.get("totalOutputAmount") or res.get("outputAmountResult") or out or 0)
            else:
                sig=""; status="SHADOW_OK"
            append_row(self.settings.csv_dir/"executions.csv",EXEC_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"mode":mode,"status":status,"signature":sig,"input_raw":order.amount_raw,"output_raw":out,"reason":order.reason,"error":""})
            return {"status":status,"mode":mode,"signature":sig,"output_raw":out,"input_raw":order.amount_raw,"order":order,"jupiter":q}
        except Exception as exc:
            detail=safe_execution_error(exc)
            append_row(self.settings.csv_dir/"executions.csv",EXEC_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"mode":mode,"status":"FAILED","signature":"","input_raw":order.amount_raw,"output_raw":0,"reason":order.reason,"error":detail})
            # Re-raise only the sanitised label so upstream logging/Telegram
            # cannot accidentally expose a URL, query string or credential.
            raise RuntimeError(detail) from None
