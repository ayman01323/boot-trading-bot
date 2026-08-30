from __future__ import annotations

import inspect
import json
import re
import time
from urllib.error import HTTPError, URLError

import requests

from .csvio import append_row, as_bool, read_rows
from .jupiter import order as jup_order, execute_order, quote_only, WSOL_MINT
from .wallet import WalletStore

EXEC_HEADERS=["timestamp","order_id","action","mint","mode","status","signature","input_raw","output_raw","reason","error"]
SETTLEMENT_HEADERS=[
    "timestamp","signature","order_id","action","mint","status","slot","block_time",
    "wsol_delta_raw","target_delta_raw","output_raw","native_delta_lamports",
    "network_fee_lamports","account_funding_delta_lamports","persistent_wsol_account","error",
]
TOKEN_PROGRAM_ID="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID="ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def _compact_text(value, limit=220):
    text=" ".join(str(value or "").split())
    text=re.sub(r"https?://\S+", "[url]", text, flags=re.I)
    text=re.sub(r"(?i)(api[-_]?key|authorization|bearer|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)
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


def safe_execution_error(exc):
    """Return a useful but credential-safe Stage-5 error label."""
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
    text=_compact_text(str(exc),180)
    if text and re.fullmatch(r"[A-Z0-9_ .|:-]+",text):
        return text
    return type(exc).__name__


def _raw_amount(info):
    token_amount=info.get("tokenAmount") if isinstance(info,dict) else None
    if isinstance(token_amount,dict):
        try:
            return int(token_amount.get("amount") or 0)
        except Exception:
            return 0
    try:
        return int((info or {}).get("amount") or 0)
    except Exception:
        return 0


def _account_keys(tx):
    keys=((((tx or {}).get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    out=[]
    for key in keys:
        if isinstance(key,dict):
            out.append(str(key.get("pubkey") or ""))
        else:
            out.append(str(key or ""))
    return out


def _all_instructions(tx):
    message=((tx or {}).get("transaction") or {}).get("message") or {}
    out=list(message.get("instructions") or [])
    meta=(tx or {}).get("meta") or {}
    for group in meta.get("innerInstructions") or []:
        out.extend(group.get("instructions") or [])
    return out


def reconcile_transaction(tx, taker: str, target_mint: str) -> dict:
    """Reconcile wallet-controlled token cashflows from confirmed jsonParsed tx data.

    WSOL transfers are summed net across token accounts owned/initialised by the
    trading wallet. This captures swap proceeds/spend and route-side token fees
    while excluding temporary WSOL rent, which is a native-account movement.
    """
    meta=(tx or {}).get("meta") or {}
    if not tx or meta.get("err") is not None:
        raise RuntimeError("SETTLEMENT_TRANSACTION_FAILED")
    keys=_account_keys(tx)
    controlled={}
    for name in ("preTokenBalances","postTokenBalances"):
        for bal in meta.get(name) or []:
            try:
                idx=int(bal.get("accountIndex"))
            except Exception:
                continue
            if idx<0 or idx>=len(keys):
                continue
            if str(bal.get("owner") or "")!=taker:
                continue
            mint=str(bal.get("mint") or "")
            if mint:
                controlled[keys[idx]]=mint

    instructions=_all_instructions(tx)
    for ins in instructions:
        parsed=ins.get("parsed") if isinstance(ins,dict) else None
        if not isinstance(parsed,dict):
            continue
        typ=str(parsed.get("type") or "")
        info=parsed.get("info") or {}
        if typ.startswith("initializeAccount") and str(info.get("owner") or "")==taker:
            account=str(info.get("account") or "")
            mint=str(info.get("mint") or "")
            if account and mint:
                controlled[account]=mint

    wsol_delta=0
    target_delta=0
    for ins in instructions:
        parsed=ins.get("parsed") if isinstance(ins,dict) else None
        if not isinstance(parsed,dict):
            continue
        if str(parsed.get("type") or "") not in {"transfer","transferChecked"}:
            continue
        info=parsed.get("info") or {}
        amount=_raw_amount(info)
        if amount<=0:
            continue
        source=str(info.get("source") or "")
        destination=str(info.get("destination") or "")
        mint=str(info.get("mint") or controlled.get(source) or controlled.get(destination) or "")
        if mint==WSOL_MINT:
            if controlled.get(source)==WSOL_MINT:
                wsol_delta-=amount
            if controlled.get(destination)==WSOL_MINT:
                wsol_delta+=amount
        if mint==target_mint:
            if controlled.get(source)==target_mint:
                target_delta-=amount
            if controlled.get(destination)==target_mint:
                target_delta+=amount

    by_index={}
    for name,sign in (("preTokenBalances",-1),("postTokenBalances",1)):
        for bal in meta.get(name) or []:
            if str(bal.get("owner") or "")!=taker:
                continue
            try:
                idx=int(bal.get("accountIndex")); amount=int(((bal.get("uiTokenAmount") or {}).get("amount")) or 0)
            except Exception:
                continue
            key=(idx,str(bal.get("mint") or ""))
            by_index[key]=by_index.get(key,0)+sign*amount
    balance_wsol=sum(v for (_,m),v in by_index.items() if m==WSOL_MINT)
    balance_target=sum(v for (_,m),v in by_index.items() if m==target_mint)
    if wsol_delta==0 and balance_wsol!=0:
        wsol_delta=balance_wsol
    if target_delta==0 and balance_target!=0:
        target_delta=balance_target

    fee=int(meta.get("fee") or 0)
    native_delta=0
    try:
        wallet_index=keys.index(taker)
        native_delta=int((meta.get("postBalances") or [])[wallet_index])-int((meta.get("preBalances") or [])[wallet_index])
    except Exception:
        native_delta=0
    account_funding_delta=native_delta-wsol_delta+fee
    return {
        "status":"CONFIRMED",
        "slot":int((tx or {}).get("slot") or 0),
        "block_time":int((tx or {}).get("blockTime") or 0),
        "wsol_delta_raw":int(wsol_delta),
        "target_delta_raw":int(target_delta),
        "native_delta_lamports":int(native_delta),
        "network_fee_lamports":fee,
        "account_funding_delta_lamports":int(account_funding_delta),
    }


def realised_cycle_pnl_lamports(buy_settlement: dict, sell_settlement: dict) -> int:
    return int(buy_settlement.get("wsol_delta_raw") or 0)+int(sell_settlement.get("wsol_delta_raw") or 0)-int(buy_settlement.get("network_fee_lamports") or 0)-int(sell_settlement.get("network_fee_lamports") or 0)


def account_funding_delta_lamports(settlement: dict, persistent_wsol: bool=False) -> int:
    """Return native SOL movement excluding trade cashflow and network fee.

    Legacy mode wraps/unwraps native SOL inside each transaction, so WSOL flow
    must be removed from native balance movement. Persistent-WSOL mode keeps
    trade cashflow entirely in the token account, so only native delta + fee
    represents account funding/refunds.
    """
    native=int(settlement.get("native_delta_lamports") or 0)
    fee=int(settlement.get("network_fee_lamports") or 0)
    if persistent_wsol:
        return native+fee
    wsol=int(settlement.get("wsol_delta_raw") or 0)
    return native-wsol+fee


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

    @staticmethod
    def _success(row):
        return str(row.get("status") or "").upper().startswith("SUCCESS") and bool(str(row.get("signature") or "").strip())

    def _same_mint_cooldown_seconds(self):
        try:
            return max(0,int(float(self.settings.runtime().get("same_mint_reentry_cooldown_seconds") or 60)))
        except Exception:
            return 60

    def _entry_guard(self, order):
        if str(order.action or "").upper()!="BUY":
            return
        executions=read_rows(self.settings.csv_dir/"executions.csv")
        by_order={str(r.get("order_id") or ""):r for r in executions}
        prior=by_order.get(str(order.order_id or ""))
        if prior and self._success(prior):
            raise RuntimeError("DUPLICATE_ENTRY_BLOCKED")

        opportunity=str(order.opportunity_id or "")
        if opportunity:
            for logged in read_rows(self.settings.csv_dir/"orders.csv"):
                if str(logged.get("action") or "").upper()!="BUY" or str(logged.get("opportunity_id") or "")!=opportunity:
                    continue
                done=by_order.get(str(logged.get("order_id") or ""))
                if done and self._success(done):
                    raise RuntimeError("DUPLICATE_ENTRY_BLOCKED")

        for pos in read_rows(self.settings.csv_dir/"open_positions.csv"):
            if str(pos.get("status") or "").upper()=="OPEN" and str(pos.get("mint") or "")==str(order.mint or ""):
                raise RuntimeError("DUPLICATE_ENTRY_BLOCKED")

        cooldown=self._same_mint_cooldown_seconds()
        if cooldown<=0:
            return
        now=int(time.time())
        for row in reversed(executions):
            if str(row.get("action") or "").upper()!="SELL" or str(row.get("mint") or "")!=str(order.mint or "") or not self._success(row):
                continue
            try:
                age=now-int(float(row.get("timestamp") or 0))
            except Exception:
                age=cooldown+1
            if age<cooldown:
                raise RuntimeError("SAME_MINT_REENTRY_COOLDOWN")
            break

    def _rpc(self, method, params, timeout=12):
        rpc=self.settings.resolve_rpc("http")
        r=requests.post(rpc,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=timeout)
        r.raise_for_status()
        body=r.json() or {}
        if body.get("error"):
            raise RuntimeError("SOLANA_RPC_ERROR")
        return body.get("result")

    def _confirmed_transaction(self, signature, wait_seconds=20):
        deadline=time.time()+max(1,int(wait_seconds))
        while time.time()<deadline:
            result=self._rpc("getTransaction",[signature,{"encoding":"jsonParsed","commitment":"confirmed","maxSupportedTransactionVersion":0}])
            if result:
                return result
            time.sleep(1)
        return None

    def _append_settlement(self, order, signature, settlement, persistent_account="", error=""):
        row={
            "timestamp":int(time.time()),"signature":signature,"order_id":order.order_id,"action":order.action,
            "mint":order.mint,"status":str((settlement or {}).get("status") or "PENDING"),
            "slot":int((settlement or {}).get("slot") or 0),"block_time":int((settlement or {}).get("block_time") or 0),
            "wsol_delta_raw":int((settlement or {}).get("wsol_delta_raw") or 0),
            "target_delta_raw":int((settlement or {}).get("target_delta_raw") or 0),
            "output_raw":int((settlement or {}).get("output_raw") or 0),
            "native_delta_lamports":int((settlement or {}).get("native_delta_lamports") or 0),
            "network_fee_lamports":int((settlement or {}).get("network_fee_lamports") or 0),
            "account_funding_delta_lamports":int((settlement or {}).get("account_funding_delta_lamports") or 0),
            "persistent_wsol_account":persistent_account,"error":_compact_text(error,160),
        }
        append_row(self.settings.csv_dir/"settlements.csv",SETTLEMENT_HEADERS,row)

    def _derive_wsol_ata(self, owner):
        try:
            from solders.pubkey import Pubkey
            owner_pk=Pubkey.from_string(owner)
            mint_pk=Pubkey.from_string(WSOL_MINT)
            token_pk=Pubkey.from_string(TOKEN_PROGRAM_ID)
            ata_program=Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)
            ata,_=Pubkey.find_program_address([bytes(owner_pk),bytes(token_pk),bytes(mint_pk)],ata_program)
            return str(ata)
        except Exception as exc:
            raise RuntimeError("PERSISTENT_WSOL_DERIVATION_UNAVAILABLE") from exc

    def _persistent_wsol_preflight(self, taker, order):
        rt=self.settings.runtime()
        if not as_bool(rt.get("persistent_wsol_enabled"),False):
            return ""
        derived=self._derive_wsol_ata(taker)
        configured=str(rt.get("persistent_wsol_account") or "").strip()
        if configured and configured!=derived:
            raise RuntimeError("PERSISTENT_WSOL_ACCOUNT_MISMATCH")
        value=(self._rpc("getAccountInfo",[derived,{"encoding":"jsonParsed","commitment":"confirmed"}]) or {}).get("value")
        if not value:
            raise RuntimeError("PERSISTENT_WSOL_ACCOUNT_MISSING")
        if str(value.get("owner") or "")!=TOKEN_PROGRAM_ID:
            raise RuntimeError("PERSISTENT_WSOL_TOKEN_PROGRAM_MISMATCH")
        info=((((value.get("data") or {}).get("parsed") or {}).get("info")) or {})
        if str(info.get("mint") or "")!=WSOL_MINT or str(info.get("owner") or "")!=taker:
            raise RuntimeError("PERSISTENT_WSOL_OWNERSHIP_MISMATCH")
        close_authority=str(info.get("closeAuthority") or "").strip()
        if close_authority and close_authority!=taker:
            raise RuntimeError("PERSISTENT_WSOL_CLOSE_AUTHORITY_MISMATCH")
        try:
            balance=int(((info.get("tokenAmount") or {}).get("amount")) or 0)
        except Exception:
            balance=0
        try:
            min_remaining=int(float(rt.get("persistent_wsol_min_balance_sol") or 0)*1e9)
        except Exception:
            min_remaining=0
        if str(order.action or "").upper()=="BUY" and balance<int(order.amount_raw)+min_remaining:
            raise RuntimeError("INSUFFICIENT_PERSISTENT_WSOL")
        try:
            reserve=int(float(rt.get("native_fee_reserve_sol") or 0.005)*1e9)
        except Exception:
            reserve=5_000_000
        native=int((self._rpc("getBalance",[taker,{"commitment":"confirmed"}]) or {}).get("value") or 0)
        if native<reserve:
            raise RuntimeError("NATIVE_FEE_RESERVE_TOO_LOW")
        return derived

    @staticmethod
    def _persistent_jupiter_kwargs(order, persistent_account):
        if not persistent_account:
            return {}
        params=inspect.signature(jup_order).parameters
        has_var_kwargs=any(p.kind==inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs={}
        wrap_key=next((k for k in ("wrap_and_unwrap_sol","wrapAndUnwrapSol") if k in params),None)
        if wrap_key:
            kwargs[wrap_key]=False
        elif "swap_options" in params:
            kwargs["swap_options"]={"wrapAndUnwrapSol":False}
        elif "options" in params:
            kwargs["options"]={"wrapAndUnwrapSol":False}
        elif has_var_kwargs:
            kwargs["wrap_and_unwrap_sol"]=False
        else:
            raise RuntimeError("PERSISTENT_WSOL_BUILDER_UNSUPPORTED")

        if str(order.action or "").upper()=="BUY":
            key=next((k for k in ("source_token_account","sourceTokenAccount") if k in params),None)
            if key:
                kwargs[key]=persistent_account
            elif has_var_kwargs:
                kwargs["source_token_account"]=persistent_account
        else:
            key=next((k for k in ("destination_token_account","destinationTokenAccount") if k in params),None)
            if key:
                kwargs[key]=persistent_account
            elif "swap_options" in kwargs:
                kwargs["swap_options"]["destinationTokenAccount"]=persistent_account
            elif "options" in kwargs:
                kwargs["options"]["destinationTokenAccount"]=persistent_account
            elif has_var_kwargs:
                kwargs["destination_token_account"]=persistent_account
        return kwargs

    def execute(self, order):
        rt=self.settings.runtime(); live=as_bool(rt.get("live_enabled"),False); broadcast=as_bool(rt.get("broadcast_enabled"),False)
        manual=as_bool(rt.get("manual_approval_enabled"),False)
        external=as_bool(rt.get("manual_approval_require_external_signature"),True)
        if live and broadcast and manual and external:
            raise RuntimeError("MANUAL_APPROVAL_EXTERNAL_SIGNATURE_REQUIRED")

        wallet=WalletStore(self.settings); taker=wallet.address(); mode="LIVE" if live and broadcast else "SHADOW"
        input_mint=WSOL_MINT if order.action=="BUY" else order.mint
        output_mint=order.mint if order.action=="BUY" else WSOL_MINT
        persistent_account=""
        try:
            self._entry_guard(order)
            if mode=="SHADOW":
                q=quote_only(self.settings,taker,input_mint,output_mint,order.amount_raw)
            else:
                persistent_account=self._persistent_wsol_preflight(taker,order)
                kwargs=self._persistent_jupiter_kwargs(order,persistent_account)
                q=jup_order(self.settings,taker,input_mint,output_mint,order.amount_raw,**kwargs)

            quoted_out=self._output_raw(q)
            if quoted_out<=0:
                raise RuntimeError("JUPITER_NO_EXECUTABLE_OUTPUT")

            if mode=="LIVE":
                if not wallet.has_private_key(): raise RuntimeError("SIGNER_NOT_READY")
                res=execute_order(self.settings,q,wallet.keypair_bytes())
                sig=str(res.get("signature") or "")
                if not sig:
                    raise RuntimeError("MISSING_EXECUTION_SIGNATURE")
                tx=self._confirmed_transaction(sig,wait_seconds=20)
                settlement=None
                settlement_error=""
                if tx:
                    try:
                        settlement=reconcile_transaction(tx,taker,str(order.mint or ""))
                        settlement["account_funding_delta_lamports"]=account_funding_delta_lamports(settlement,bool(persistent_account))
                        settled_out=max(0,int(settlement["target_delta_raw"] if order.action=="BUY" else settlement["wsol_delta_raw"]))
                        settlement["output_raw"]=settled_out
                        if settled_out<=0:
                            raise RuntimeError("SETTLEMENT_OUTPUT_NOT_POSITIVE")
                    except Exception as exc:
                        settlement=None; settlement_error=safe_execution_error(exc)
                else:
                    settlement_error="SETTLEMENT_PENDING"
                self._append_settlement(order,sig,settlement,persistent_account,settlement_error)
                execution_out=int(res.get("totalOutputAmount") or res.get("outputAmountResult") or quoted_out or 0)
                out=int((settlement or {}).get("output_raw") or execution_out)
                persisted_out=int((settlement or {}).get("output_raw") or 0)
                status="SUCCESS" if settlement else "SUCCESS_UNSETTLED"
                error="" if settlement else settlement_error
            else:
                sig=""; status="SHADOW_OK"; out=quoted_out; persisted_out=out; error=""

            append_row(self.settings.csv_dir/"executions.csv",EXEC_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"mode":mode,"status":status,"signature":sig,"input_raw":order.amount_raw,"output_raw":persisted_out,"reason":order.reason,"error":error})
            return {"status":status,"mode":mode,"signature":sig,"output_raw":out,"settled_output_raw":persisted_out,"settlement_status":"CONFIRMED" if status=="SUCCESS" else ("PENDING" if status=="SUCCESS_UNSETTLED" else "SHADOW"),"input_raw":order.amount_raw,"order":order,"jupiter":q,"persistent_wsol_account":persistent_account}
        except Exception as exc:
            detail=safe_execution_error(exc)
            append_row(self.settings.csv_dir/"executions.csv",EXEC_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"mode":mode,"status":"FAILED","signature":"","input_raw":order.amount_raw,"output_raw":0,"reason":order.reason,"error":detail})
            raise RuntimeError(detail) from None
