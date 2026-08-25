from __future__ import annotations

import importlib
import os
import queue
import time
import traceback
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any, Iterable

from .contracts import ExitIntent, MarketEvent, TradeIntent


def _iter_intents(value: Any) -> Iterable[TradeIntent | ExitIntent]:
    if value is None:
        return ()
    if isinstance(value, (TradeIntent, ExitIntent)):
        return (value,)
    if isinstance(value, (tuple, list)):
        return tuple(item for item in value if isinstance(item, (TradeIntent, ExitIntent)))
    return ()


def run_engine_worker(engine_id: str, settings_path: str, runtime_dir: str, inbox: Queue, outbox: Queue) -> None:
    """Child-process entry point for exactly one independent strategy engine."""
    try:
        module = importlib.import_module(f"sibot1_engines.{engine_id}.engine")
        engine = module.build_engine(Path(settings_path), Path(runtime_dir) / engine_id)
        outbox.put(("READY", engine_id, {"pid": os.getpid(), **dict(engine.health())}))
    except Exception as exc:
        outbox.put(("FATAL", engine_id, {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}))
        return

    while True:
        try:
            kind, payload = inbox.get(timeout=1.0)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        if kind == "STOP":
            outbox.put(("STOPPED", engine_id, {"pid": os.getpid(), **dict(engine.health())}))
            return
        try:
            if kind == "MARKET" and isinstance(payload, MarketEvent):
                result = engine.on_market_event(payload)
            elif kind == "POSITION" and isinstance(payload, dict):
                result = engine.on_position_update(payload)
            elif kind == "HEALTH":
                outbox.put(("HEALTH", engine_id, {"pid": os.getpid(), **dict(engine.health())}))
                continue
            else:
                continue
            for intent in _iter_intents(result):
                outbox.put(("INTENT", engine_id, intent))
            outbox.put(("HEALTH", engine_id, {"pid": os.getpid(), **dict(engine.health()), "updated_epoch": int(time.time())}))
        except Exception as exc:
            outbox.put(("ERROR", engine_id, {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}))
