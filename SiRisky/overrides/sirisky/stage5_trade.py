from __future__ import annotations

import time
from .csvio import append_row, as_bool
from .jupiter import order as jup_order, execute_order, quote_only, WSOL_MINT
from .wallet import WalletStore

EXEC_HEADERS=["timestamp","order_id","action","mint","mode","status","signature","input_raw","output_raw","reason","error"]

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
            append_row(self.settings.csv_dir/"executions.csv",EXEC_HEADERS,{"timestamp":int(time.time()),"order_id":order.order_id,"action":order.action,"mint":order.mint,"mode":mode,"status":"FAILED","signature":"","input_raw":order.amount_raw,"output_raw":0,"reason":order.reason,"error":type(exc).__name__})
            raise
