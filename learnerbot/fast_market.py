from __future__ import annotations

import threading
import time
from pathlib import Path

from .auto_trader import execute_best_live_opportunity
from .challenge_alerts import send_target_test_once
from .config import AppSettings, load_kv_scoped
from .market_scanner import _atomic_rows, _rows, merge_live_opportunities
from .full_power_scanner import scan_full_power_hot_routes
from .multichain import close_contexts, contexts
from .telegram import send_to_chats

STATUS_HEADERS=["updated_epoch","duration_seconds","routes","merged_routes","eligible","auto_events","status","note"]
_started=False
_lock=threading.Lock()


def _bool(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}


def _write_status(app, **values):
    row={h:values.get(h,"") for h in STATUS_HEADERS}
    _atomic_rows(Path(app.csv_dir)/"auto"/"fast_market_status.csv",[row],STATUS_HEADERS)


def _notify_auto_event(app, ev):
    if not app.telegram_bot_token or not ev.get("telegram_id"):return
    if ev.get("status") not in {"BROADCAST","SUCCESS","SUCCESS_FEE_PENDING","FAILED","FEE_PENDING"}:return
    try:
        send_to_chats(app.telegram_bot_token,[str(ev.get("telegram_id"))],
            f"⚡ FAST AUTO ROUTE {ev.get('status')}\nChain: {str(ev.get('chain_slug') or '').upper()}\n"
            f"Input: {ev.get('input_base')}\nRealised net: {ev.get('realised_net_base') or ev.get('expected_net_base')}\n"
            f"TX: {ev.get('tx_hash') or '-'}")
    except Exception:
        pass


def run_fast_market_pass(app):
    """Run one independent current-market pass and return a compact status dict.

    The function is deliberately separate from the thread loop so the full fast path
    can be unit-tested without sleeping or starting systemd/Telegram.
    """
    ctxs=[];started=time.monotonic()
    try:
        ctxs=contexts(app,enabled_only=True,with_rpc=False)
        market_path,market_rows,_power_rejections=scan_full_power_hot_routes(app,ctxs)
        learned_rows=_rows(Path(app.csv_dir)/"auto"/"learned_route_opportunities.csv")
        opp_path,live_rows=merge_live_opportunities(app,learned_rows,market_rows)
        eligible=sum(1 for r in live_rows if str(r.get("enabled") or "").lower()=="true")
        events=execute_best_live_opportunity(app,live_rows)
        for ev in events:_notify_auto_event(app,ev)
        duration=time.monotonic()-started
        result={"updated_epoch":int(time.time()),"duration_seconds":f"{duration:.3f}","routes":len(market_rows),
                "merged_routes":len(live_rows),"eligible":eligible,"auto_events":len(events),"status":"OK","note":str(market_path)}
        _write_status(app,**result)
        return result
    finally:
        close_contexts(ctxs)


def _loop(initial_app):
    time.sleep(2)
    while True:
        started=time.monotonic();app=initial_app
        try:
            app=AppSettings.load();cfg=load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",0)
            interval=max(5,min(300,int(float(cfg.get("fast_market_interval_seconds","15") or 15))))
            if not _bool(cfg.get("fast_market_enabled","true"),True):
                _write_status(app,updated_epoch=int(time.time()),duration_seconds="0",routes=0,merged_routes=0,eligible=0,auto_events=0,status="DISABLED",note="fast_market_enabled=false")
                time.sleep(interval);continue
            if not _bool(app.operator_settings().get("engine_enabled","true"),True):
                _write_status(app,updated_epoch=int(time.time()),duration_seconds="0",routes=0,merged_routes=0,eligible=0,auto_events=0,status="PAUSED",note="operator engine disabled")
                time.sleep(interval);continue
            result=run_fast_market_pass(app)
            print(f"[fast-market-scan] direct={result['routes']} merged={result['merged_routes']} eligible={result['eligible']} auto-events={result['auto_events']} seconds={result['duration_seconds']}",flush=True)
        except Exception as exc:
            duration=time.monotonic()-started
            try:_write_status(app,updated_epoch=int(time.time()),duration_seconds=f"{duration:.3f}",routes=0,merged_routes=0,eligible=0,auto_events=0,status="ERROR",note=f"{type(exc).__name__}:{str(exc)[:300]}")
            except Exception:pass
            print(f"[fast-market-error] {type(exc).__name__}: {exc}",flush=True)
        try:
            cfg=load_kv_scoped(Path(AppSettings.load().csv_dir)/"auto_trading_settings.csv",0)
            interval=max(5,min(300,int(float(cfg.get("fast_market_interval_seconds","15") or 15))))
        except Exception:interval=15
        elapsed=time.monotonic()-started;time.sleep(max(1,interval-elapsed))


def start_fast_market_thread(app):
    global _started
    with _lock:
        if _started:return None
        try:
            test_result=send_target_test_once(app)
            print(
                f"[challenge-target-test] status={test_result.get('status')} "
                f"recipients={test_result.get('recipient_count',0)} "
                f"sent={test_result.get('sent_chats',0)} failed={test_result.get('failed_chats',0)}",
                flush=True,
            )
        except Exception as exc:
            print(f"[challenge-target-test-error] {type(exc).__name__}: {exc}",flush=True)
        t=threading.Thread(target=_loop,args=(app,),name="fast-market-scanner",daemon=True);t.start();_started=True;return t
