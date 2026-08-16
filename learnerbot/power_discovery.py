from __future__ import annotations
import threading,time
from pathlib import Path
from .config import AppSettings,load_kv_scoped
from .full_power_scanner import discover_full_power_pools
from .multichain import contexts,close_contexts
from .product_universe import refresh_product_universe, universe_summary

_started=False
_lock=threading.Lock()

def _bool(v,default=False):
    if v is None:return default
    return str(v).strip().lower() in {'1','true','yes','on','y'}

def _loop(initial_app):
    time.sleep(1)
    while True:
        app=initial_app;ctxs=[];started=time.monotonic()
        try:
            app=AppSettings.load();cfg=load_kv_scoped(Path(app.csv_dir)/'auto_trading_settings.csv',0)
            interval=max(30,min(1800,int(float(cfg.get('full_power_discovery_interval_seconds','120') or 120))))
            if _bool(cfg.get('full_power_enabled','true'),True) and _bool(cfg.get('v3_scanner_enabled','true'),True):
                ctxs=contexts(app,enabled_only=True,with_rpc=False);result=discover_full_power_pools(app,ctxs)
                products=refresh_product_universe(app,ctxs);psum=universe_summary(app.csv_dir)
                print(f"[power-discovery] v2-added={result.get('v2_pools_added',0)} v3-pools={result['v3_pools_seen']} products={psum.get('total',len(products))} auto-products={psum.get('trade',0)} rejected={result['rejected']} seconds={time.monotonic()-started:.3f}",flush=True)
        except Exception as exc:
            print(f'[power-discovery-error] {type(exc).__name__}: {exc}',flush=True)
        finally:
            close_contexts(ctxs)
        try:
            cfg=load_kv_scoped(Path(AppSettings.load().csv_dir)/'auto_trading_settings.csv',0);interval=max(30,min(1800,int(float(cfg.get('full_power_discovery_interval_seconds','120') or 120))))
        except Exception:interval=120
        time.sleep(max(5,interval-(time.monotonic()-started)))

def start_power_discovery_thread(app):
    global _started
    with _lock:
        if _started:return None
        t=threading.Thread(target=_loop,args=(app,),name='power-pool-discovery',daemon=True);t.start();_started=True;return t
