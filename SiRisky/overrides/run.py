from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from sirisky.config import Settings
from sirisky.csvio import as_bool
from sirisky.engine import SiRiskyEngine
from sirisky.jupiter import quote_only, WSOL_MINT, USDC_MINT, wallet_balance_lamports
from sirisky.manual_approval import ManualApprovalGate
from sirisky.rpc import rpc_call
from sirisky.telegram import TelegramClient
from sirisky.wallet import WalletStore


def status_line(ok,name,detail=""):
    tag="PASS" if ok else "FAIL"; print(f"[{tag}] {name}"+(f" — {detail}" if detail else "")); return ok


def safe_check(settings):
    results=[]
    results.append(status_line(settings.csv_dir.exists(),"CSV directory"))
    try:
        v=rpc_call(settings,"getHealth",[]); results.append(status_line(v=="ok" or v is not None,"Solana RPC connectivity"))
    except Exception as exc: results.append(status_line(False,"Solana RPC connectivity",type(exc).__name__))
    store=WalletStore(settings)
    try:
        addr=store.address(); results.append(status_line(bool(addr),"wallet metadata"))
        try:
            bal=wallet_balance_lamports(settings,addr); results.append(status_line(bal>=0,"wallet balance read"))
        except Exception as exc: results.append(status_line(False,"wallet balance read",type(exc).__name__))
        results.append(status_line(store.has_private_key(),"signing readiness"))
        try:
            q1=quote_only(settings,addr,WSOL_MINT,USDC_MINT,100_000); results.append(status_line(int(q1.get("out_amount") or 0)>0,"Jupiter buy-side quote"))
            if int(q1.get("out_amount") or 0)>0:
                q2=quote_only(settings,addr,USDC_MINT,WSOL_MINT,int(q1["out_amount"])); results.append(status_line(int(q2.get("out_amount") or 0)>0,"Jupiter sell-side quote"))
        except Exception as exc: results.append(status_line(False,"Jupiter round-trip quote",type(exc).__name__))
    except Exception as exc:
        results.append(status_line(False,"wallet metadata",type(exc).__name__))
    tg=TelegramClient(settings)
    if tg.configured(): results.append(status_line(tg.send("SiRisky preflight: Telegram delivery test. Transaction broadcast remains locked behind external/manual signing."),"Telegram delivery"))
    else: print("[SKIP] Telegram delivery — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_IDS not fully configured")
    rt=settings.runtime(); live=as_bool(rt.get("live_enabled"),False); broadcast=as_bool(rt.get("broadcast_enabled"),False); manual=as_bool(rt.get("manual_approval_enabled"),False)
    print(f"[PASS] live gate report — live_enabled={str(live).lower()} broadcast_enabled={str(broadcast).lower()} manual_approval_enabled={str(manual).lower()}")
    return 0 if all(results) else 2


def _pending_text(engine, settings):
    rows=engine.pending_approvals()
    if not rows: return "SiRisky: no active manual-approval proposals."
    gate=ManualApprovalGate(settings)
    return "\n\n".join(gate.format_for_user(r) for r in rows[:3])[:3500]


def telegram_handler(engine, settings, cmd, chat):
    if cmd=="/status":
        return f"SiRisky status\nOpen positions: {len(engine.open_positions())}\nPending approvals: {len(engine.pending_approvals())}\nLIVE: {settings.runtime().get('live_enabled','0')}\nBroadcast: {settings.runtime().get('broadcast_enabled','0')}\nManual approval: {settings.runtime().get('manual_approval_enabled','0')}"
    if cmd=="/pending": return _pending_text(engine,settings)
    if cmd=="/check": return "Run server-side: python run.py check (safe, no broadcast)."
    if cmd=="/runone":
        if not as_bool(settings.runtime().get("telegram_manual_run_enabled"),False): return "SiRisky /runone is disabled in CSV."
        try:return json.dumps(engine.run_once(),default=str)[:3500]
        except Exception as exc:return f"SiRisky cycle failed: {type(exc).__name__}"
    return "SiRisky commands: /status /pending /check /runone"


def start(settings):
    engine=SiRiskyEngine(settings); tg=TelegramClient(settings); stop=threading.Event(); tg.run_thread(lambda c,ch:telegram_handler(engine,settings,c,ch),stop)
    tg.send("SiRisky started. Manual per-trade approval is supported; server-side transaction broadcast remains CSV-gated and external/manual signing is required when the approval gate is enabled.")
    def sig(*_): stop.set()
    signal.signal(signal.SIGTERM,sig); signal.signal(signal.SIGINT,sig)
    while not stop.is_set():
        try:
            result=engine.run_once(); status=str(result.get("status") or "")
            if status=="WAITING_FOR_MANUAL_APPROVAL":
                if result.get("new_proposal") and result.get("proposal"):
                    tg.send(ManualApprovalGate(settings).format_for_user(result["proposal"]))
            elif status in {"OPENED","CLOSED","RISK_REJECT","MANUAL_APPROVAL_PREP_FAILED"}:
                tg.send("SiRisky: "+json.dumps(result,default=str)[:3500])
        except Exception as exc:
            tg.send(f"SiRisky cycle error: {type(exc).__name__}")
        delay=float(settings.runtime().get("poll_seconds") or 5); stop.wait(max(1.0,delay))
    return 0


def selftest():
    import unittest
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent/"tests"))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main():
    p=argparse.ArgumentParser(prog="SiRisky"); sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("check"); sub.add_parser("selftest"); sub.add_parser("start"); sub.add_parser("once"); sub.add_parser("wallet-status"); sub.add_parser("approvals")
    w=sub.add_parser("wallet-import"); w.add_argument("--stdin",action="store_true",required=True)
    args=p.parse_args(); settings=Settings.load()
    if args.cmd=="check": return safe_check(settings)
    if args.cmd=="selftest": return selftest()
    if args.cmd=="start": return start(settings)
    if args.cmd=="once":
        print(json.dumps(SiRiskyEngine(settings).run_once(),default=str,indent=2)); return 0
    if args.cmd=="approvals":
        rows=SiRiskyEngine(settings).pending_approvals()
        print(json.dumps(rows,default=str,indent=2)); return 0
    if args.cmd=="wallet-status":
        s=WalletStore(settings)
        try: print(json.dumps({"address":s.address(),"signer_ready":s.has_private_key()},indent=2)); return 0
        except Exception as exc: print(json.dumps({"signer_ready":False,"error":type(exc).__name__})); return 2
    if args.cmd=="wallet-import":
        secret=sys.stdin.read().strip()
        if not secret: raise SystemExit("No keypair received on stdin")
        meta=WalletStore(settings).import_key(secret); print(json.dumps({"wallet_id":meta["wallet_id"],"address":meta["address"],"stored":"encrypted"},indent=2)); return 0
    return 2

if __name__=="__main__": raise SystemExit(main())
