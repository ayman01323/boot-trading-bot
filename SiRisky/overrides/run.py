from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from sirisky.config import Settings
from sirisky.csvio import as_bool
from sirisky.engine import SiRiskyEngine
from sirisky.jupiter import quote_only, WSOL_MINT, USDC_MINT, wallet_balance_lamports
from sirisky.manual_approval import ManualApprovalGate
from sirisky.position_telegram import PositionTelegramReporter
from sirisky.rpc import rpc_call
from sirisky.telegram import TelegramClient
from sirisky.wallet import WalletStore


def status_line(ok,name,detail=""):
    tag="PASS" if ok else "FAIL"; print(f"[{tag}] {name}"+(f" — {detail}" if detail else "")); return ok


def _safe_trace(exc):
    """Return only file/line/function frames; never include exception text/URLs."""
    frames=traceback.extract_tb(exc.__traceback__)
    compact=[]
    for frame in frames[-6:]:
        compact.append(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}")
    return " > ".join(compact) or "no-trace"


def safe_check(settings):
    rt=settings.runtime(); live=as_bool(rt.get("live_enabled"),False); broadcast=as_bool(rt.get("broadcast_enabled"),False); manual=as_bool(rt.get("manual_approval_enabled"),False)
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
            q1=quote_only(settings,addr,WSOL_MINT,USDC_MINT,100_000)
            buy_ok=int(q1.get("out_amount") or 0)>0
            status_line(buy_ok,"Jupiter buy-side quote")
            sell_ok=False
            if buy_ok:
                q2=quote_only(settings,addr,USDC_MINT,WSOL_MINT,int(q1["out_amount"]))
                sell_ok=int(q2.get("out_amount") or 0)>0
                status_line(sell_ok,"Jupiter sell-side quote")
            if broadcast:
                results.append(buy_ok and sell_ok)
            elif not (buy_ok and sell_ok):
                print("[WARN] Jupiter preflight unavailable; non-fatal because broadcast_enabled=false")
        except Exception as exc:
            status_line(False,"Jupiter round-trip quote",type(exc).__name__)
            if broadcast:
                results.append(False)
            else:
                print("[WARN] Jupiter preflight unavailable; non-fatal because broadcast_enabled=false")
    except Exception as exc:
        results.append(status_line(False,"wallet metadata",type(exc).__name__))
    tg=TelegramClient(settings)
    if tg.configured(): results.append(status_line(tg.send("SiRisky preflight: Telegram delivery test. Transaction broadcast remains locked behind external/manual signing."),"Telegram delivery"))
    else: print("[SKIP] Telegram delivery — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_IDS not fully configured")
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


def _fmt_pct(value):
    try:return f"{float(value):+.4f}%"
    except Exception:return "n/a"


def _fmt_health(value):
    try:return f"{float(value):.4f}%"
    except Exception:return "n/a"


def _format_result_notice(result,settings):
    status=str(result.get("status") or "")
    if status=="CANDIDATE_BATCH_NO_OPEN":
        attempted=int(result.get("attempted_candidates") or 0)
        lines=[
            "SiRisky candidate batch: NO OPEN",
            f"Attempted: {attempted} | Stage 3 passed: {int(result.get('stage3_passed_candidates') or 0)} | Stage 5 failed: {int(result.get('execution_rejects') or 0)} | Risk rejected: {int(result.get('risk_rejects') or 0)}",
        ]
        for row in result.get("candidate_results") or []:
            idx=int(row.get("candidate") or 0)
            row_status=str(row.get("status") or "")
            if row.get("stage3_passed") is True and row_status=="EXECUTION_REJECT":
                path="S3 PASS → S5 FAILED"
            elif row.get("stage3_passed") is True:
                path="S3 PASS"
            elif row.get("stage3_passed") is False:
                path="S3 REJECT"
            else:
                path=row_status or "NOT EVALUATED"
            lines.extend([
                "",
                f"Candidate {idx}/{attempted}: {path}",
                f"Mint: {row.get('mint') or 'n/a'}",
                f"Pool: {row.get('pool_id') or 'n/a'}",
            ])
            if row.get("forecast_net_pct") is not None:
                lines.append(f"Forecast: {_fmt_pct(row.get('forecast_net_pct'))} | Exit health: {_fmt_health(row.get('exit_health_pct'))}")
            reason="|".join(str(x) for x in (row.get("reasons") or [])) or str(row.get("error") or "")
            if reason: lines.append(f"Reason: {reason[:260]}")
        return "\n".join(lines)[:3500]

    if status in {"RISK_REJECT","EXECUTION_REJECT"}:
        required=result.get("min_forecast_net_pct")
        if required is None:
            required=(settings.risk().get("min_forecast_net_pct") or 0.25)
        lines=[
            f"SiRisky: {status}",
            f"Mint: {result.get('mint') or 'n/a'}",
            f"Pool: {result.get('pool_id') or 'n/a'}",
            f"Stage 3: {'PASS' if result.get('stage3_passed') is True else 'REJECT'}",
            f"Forecast: {_fmt_pct(result.get('forecast_net_pct'))} | Required: {_fmt_pct(required)}",
            f"Exit health: {_fmt_health(result.get('exit_health_pct'))}",
        ]
        reason="|".join(str(x) for x in (result.get("reasons") or [])) or str(result.get("error") or "")
        if reason: lines.append(f"Reason: {reason[:500]}")
        return "\n".join(lines)[:3500]

    if status=="EXIT_EXECUTION_RETRY":
        return (f"SiRisky: EXIT retry\nPosition: {result.get('position_id') or 'n/a'}\n"
                f"Reason: {result.get('exit_reason') or 'n/a'}\nStage 5: {result.get('error') or 'FAILED'}")[:3500]
    return "SiRisky: "+json.dumps(result,default=str)[:3500]


def _result_notice_key(result):
    status=str(result.get("status") or "")
    if status=="CANDIDATE_BATCH_NO_OPEN":
        rows=result.get("candidate_results") or []
        compact=tuple((str(r.get("mint") or ""),str(r.get("status") or ""),str(r.get("error") or ""),tuple(str(x) for x in (r.get("reasons") or []))) for r in rows)
        return (status,compact)
    if status in {"RISK_REJECT","EXECUTION_REJECT"}:
        reasons=tuple(str(x) for x in (result.get("reasons") or []))
        return (status,str(result.get("mint") or ""),str(result.get("pool_id") or ""),reasons,str(result.get("error") or ""))
    if status=="EXIT_EXECUTION_RETRY":
        return (status,str(result.get("position_id") or ""),str(result.get("error") or ""))
    if status=="MANUAL_APPROVAL_PREP_FAILED":
        return (status,str(result.get("position_id") or "ENTRY"),str(result.get("error") or ""))
    return (status,)


def _runtime_interval(settings,key,default,minimum=60.0):
    try:
        return max(float(minimum),float(settings.runtime().get(key) or default))
    except Exception:
        return max(float(minimum),float(default))


def start(settings):
    engine=SiRiskyEngine(settings); tg=TelegramClient(settings); stop=threading.Event(); tg.run_thread(lambda c,ch:telegram_handler(engine,settings,c,ch),stop)
    position_reporter=PositionTelegramReporter(settings)
    position_notice_lock=threading.Lock()
    def _dispatch_position_notices(result):
        snapshot=dict(result or {})
        def worker():
            if not position_notice_lock.acquire(blocking=False):
                return
            try:
                for message in position_reporter.messages(snapshot,engine):
                    if message:
                        tg.send(message)
            except Exception as exc:
                print(f"SiRisky Telegram position notice error: {type(exc).__name__}",file=sys.stderr,flush=True)
            finally:
                position_notice_lock.release()
        threading.Thread(target=worker,name="sirisky-position-telegram",daemon=True).start()
    tg.send("SiRisky started. Manual per-trade approval is supported; server-side transaction broadcast remains CSV-gated and external/manual signing is required when the approval gate is enabled.")
    def sig(*_): stop.set()
    signal.signal(signal.SIGTERM,sig); signal.signal(signal.SIGINT,sig)
    last_error_key=""; last_error_notice=0.0
    last_result_key=None; last_result_notice=0.0
    last_batch_notice=0.0
    last_reject_notice=0.0
    while not stop.is_set():
        try:
            result=engine.run_once(); status=str(result.get("status") or "")
            _dispatch_position_notices(result)
            if status=="WAITING_FOR_MANUAL_APPROVAL":
                if result.get("new_proposal") and result.get("proposal"):
                    tg.send(ManualApprovalGate(settings).format_for_user(result["proposal"]))
            elif status in {"OPENED","CLOSED","HOLD"}:
                # Rich BUY/SELL/NewPoll45 messages are emitted asynchronously by
                # PositionTelegramReporter so reporting can never delay trading.
                pass
            elif status=="CANDIDATE_BATCH_NO_OPEN":
                # Candidate sets and statuses can change every few seconds, which made
                # key-based de-duplication ineffective. Send at most one routine batch
                # notice per configured interval, regardless of candidate churn.
                print("SiRisky: "+json.dumps(result,default=str),flush=True)
                now=time.time()
                batch_interval=_runtime_interval(settings,"telegram_batch_notice_seconds",1800,300)
                if (now-last_batch_notice)>=batch_interval:
                    tg.send(_format_result_notice(result,settings))
                    last_batch_notice=now
            elif status in {"RISK_REJECT","EXECUTION_REJECT"}:
                # Keep detailed rejects in logs, but cap Telegram reject traffic across
                # changing pools/mints so a fast discovery loop cannot flood the chat.
                print("SiRisky: "+json.dumps(result,default=str),flush=True)
                now=time.time()
                reject_interval=_runtime_interval(settings,"telegram_reject_notice_seconds",900,300)
                if (now-last_reject_notice)>=reject_interval:
                    tg.send(_format_result_notice(result,settings))
                    last_reject_notice=now
            elif status in {"EXIT_EXECUTION_RETRY","MANUAL_APPROVAL_PREP_FAILED"}:
                # Operational failures remain more responsive, with duplicate suppression.
                print("SiRisky: "+json.dumps(result,default=str),flush=True)
                now=time.time(); key=_result_notice_key(result)
                operational_interval=_runtime_interval(settings,"telegram_operational_notice_seconds",900,300)
                if key!=last_result_key or (now-last_result_notice)>=operational_interval:
                    tg.send(_format_result_notice(result,settings))
                    last_result_key=key; last_result_notice=now
        except Exception as exc:
            trace=_safe_trace(exc); key=f"{type(exc).__name__}:{trace}"; now=time.time()
            print(f"SiRisky cycle error: {type(exc).__name__} trace={trace}",file=sys.stderr,flush=True)
            error_interval=_runtime_interval(settings,"telegram_error_notice_seconds",900,300)
            if key!=last_error_key or (now-last_error_notice)>=error_interval:
                tg.send(f"SiRisky cycle error: {type(exc).__name__}\nTrace: {trace}\nRepeated identical alerts suppressed.")
                last_error_key=key; last_error_notice=now
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
        try:
            print(json.dumps(SiRiskyEngine(settings).run_once(),default=str,indent=2)); return 0
        except Exception as exc:
            print(json.dumps({"status":"CYCLE_ERROR","error":type(exc).__name__,"trace":_safe_trace(exc)},indent=2)); return 2
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
