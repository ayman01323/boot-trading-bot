from __future__ import annotations

import threading
import time
from pathlib import Path

from .auto_trader import execute_best_live_opportunity
from .config import AppSettings, load_kv_scoped
from .market_scanner import _atomic_rows, _rows, merge_live_opportunities
from .full_power_scanner import scan_full_power_hot_routes
from . import full_power_candidate_rotation_patch as _rotation
from .multichain import close_contexts, contexts
from .telegram import send_to_chats

STATUS_HEADERS=["updated_epoch","duration_seconds","routes","merged_routes","eligible","auto_events","status","note"]
BASE_STATUS_HEADERS=["updated_epoch","duration_seconds","routes","provider_pressure","checks_budget","routes_budget","status","note"]
_started=False
_base_started=False
_lock=threading.Lock()
_base_lock=threading.Lock()
_base_pressure_streak=0


def _bool(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {"1","true","yes","on","y"}


def _write_status(app, **values):
    row={h:values.get(h,"") for h in STATUS_HEADERS}
    _atomic_rows(Path(app.csv_dir)/"auto"/"fast_market_status.csv",[row],STATUS_HEADERS)


def _write_base_status(app, **values):
    row={h:values.get(h,"") for h in BASE_STATUS_HEADERS}
    _atomic_rows(Path(app.csv_dir)/"auto"/"base_fast_market_status.csv",[row],BASE_STATUS_HEADERS)


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


def run_base_hot_market_pass(app):
    """Run one read-only Base-only quote pass; never execute a transaction."""
    ctxs=[];started=time.monotonic()
    try:
        ctxs=contexts(app,enabled_only=True,with_rpc=False)
        market_path,market_rows,_rejections,meta=_rotation.scan_base_hot_routes(app,ctxs)
        duration=time.monotonic()-started
        result={
            "updated_epoch":int(time.time()),
            "duration_seconds":f"{duration:.3f}",
            "routes":len(market_rows),
            "provider_pressure":int(meta.get("provider_pressure") or 0),
            "checks_budget":int(meta.get("checks_budget") or 0),
            "routes_budget":int(meta.get("routes_budget") or 0),
            "status":"OK",
            "note":str(market_path),
        }
        _write_base_status(app,**result)
        return result
    finally:
        close_contexts(ctxs)


def run_fast_market_pass(app):
    """Run one independent current-market pass and return a compact status dict.

    Base may be supplied by the separate hot feed. This combined pass executes no
    extra Base quotes when that mode is enabled; the scanner patch merges only fresh
    rows from the dedicated file.
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


def _base_interval(cfg):
    try:
        return max(5,min(60,int(float(cfg.get("base_hot_scanner_interval_seconds","7") or 7))))
    except Exception:
        return 7


def _base_loop(initial_app):
    global _base_pressure_streak
    time.sleep(1)
    while True:
        started=time.monotonic();app=initial_app;interval=7;next_interval=7
        try:
            app=AppSettings.load();cfg=load_kv_scoped(Path(app.csv_dir)/"auto_trading_settings.csv",0)
            interval=_base_interval(cfg);next_interval=interval
            if not _bool(cfg.get("fast_market_enabled","true"),True) or not _bool(cfg.get("base_hot_scanner_enabled","true"),True):
                _base_pressure_streak=0
                _write_base_status(app,updated_epoch=int(time.time()),duration_seconds="0",routes=0,provider_pressure=0,checks_budget=0,routes_budget=0,status="DISABLED",note="Base hot scanner disabled")
                time.sleep(interval);continue
            if not _bool(app.operator_settings().get("engine_enabled","true"),True):
                _base_pressure_streak=0
                _write_base_status(app,updated_epoch=int(time.time()),duration_seconds="0",routes=0,provider_pressure=0,checks_budget=0,routes_budget=0,status="PAUSED",note="operator engine disabled")
                time.sleep(interval);continue
            result=run_base_hot_market_pass(app)
            pressure=int(result.get("provider_pressure") or 0)
            if pressure:
                _base_pressure_streak=min(5,_base_pressure_streak+1)
                next_interval=min(120,max(15,interval*(2**_base_pressure_streak)))
            else:
                _base_pressure_streak=0
            print(
                f"[base-hot-market] routes={result['routes']} checks={result['checks_budget']} "
                f"pressure={pressure} seconds={result['duration_seconds']} next={next_interval}s",
                flush=True,
            )
        except Exception as exc:
            _base_pressure_streak=min(5,_base_pressure_streak+1)
            duration=time.monotonic()-started
            next_interval=min(120,max(15,interval*(2**_base_pressure_streak)))
            try:_write_base_status(app,updated_epoch=int(time.time()),duration_seconds=f"{duration:.3f}",routes=0,provider_pressure=1,checks_budget=0,routes_budget=0,status="ERROR",note=f"{type(exc).__name__}:{str(exc)[:300]}")
            except Exception:pass
            print(f"[base-hot-market-error] {type(exc).__name__}: {exc}",flush=True)
        elapsed=time.monotonic()-started;time.sleep(max(1,next_interval-elapsed))


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


def _start_base_hot_thread(app):
    global _base_started
    with _base_lock:
        if _base_started:return None
        t=threading.Thread(target=_base_loop,args=(app,),name="base-hot-market-scanner",daemon=True)
        t.start();_base_started=True;return t


def start_fast_market_thread(app):
    global _started
    _start_base_hot_thread(app)
    with _lock:
        if _started:return None
        t=threading.Thread(target=_loop,args=(app,),name="fast-market-scanner",daemon=True);t.start();_started=True;return t
