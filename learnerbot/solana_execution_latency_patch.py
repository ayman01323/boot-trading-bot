from __future__ import annotations

import threading
import time

from . import execution_latency as _ledger
from . import solana_execution_efficiency_patch as _eff
from . import solana_live_patch as _live
from . import solana_sibot as _sol

_ORIG_RECORD_EVENT = _sol._record_leader_event
_ORIG_CLAIM = _live._claim_attempt
_ORIG_UPDATE = _live._update_attempt
_ORIG_INNER_SWAP = _eff._PREV_SWAP
_PENDING: dict[str, dict] = {}
_LOCK = threading.RLock()


def _ms(ns: int | float | None):
    if ns is None:
        return None
    return round(float(ns) / 1_000_000.0, 3)


def record_leader_event_timed(app, wallet: str, ev: dict):
    received_perf_ns = time.perf_counter_ns()
    received_epoch_ms = time.time_ns() // 1_000_000
    row = _ORIG_RECORD_EVENT(app, wallet, ev)
    if row:
        row = dict(row)
        row["_received_perf_ns"] = received_perf_ns
        row["_strategy_start_perf_ns"] = received_perf_ns
        row["_received_epoch_ms"] = received_epoch_ms
        try:
            event_ts = int(row.get("event_ts") or 0)
            row["_receive_delay_ms"] = max(0.0, float(received_epoch_ms - event_ts * 1000)) if event_ts else None
        except Exception:
            row["_receive_delay_ms"] = None
    return row


def claim_attempt_timed(app, tid, event):
    claimed, key = _ORIG_CLAIM(app, tid, event)
    if claimed:
        now = time.perf_counter_ns()
        started = int((event or {}).get("_strategy_start_perf_ns") or now)
        with _LOCK:
            _PENDING[str(key)] = {
                "telegram_id": str(tid),
                "action": str((event or {}).get("action") or "").upper(),
                "receive_delay_ms": (event or {}).get("_receive_delay_ms"),
                "strategy_ms": _ms(max(0, now - started)),
            }
    return claimed, key


def _restore_instance_attr(obj, name: str, existed: bool, value):
    if existed:
        obj.__dict__[name] = value
    else:
        obj.__dict__.pop(name, None)


def timed_inner_swap(self, input_mint: str, output_mint: str, amount_raw: int):
    total_start = time.perf_counter_ns()
    marks: dict[str, int | float | None] = {}
    balances: list[tuple[int, int]] = []

    existed_order = "_order" in self.__dict__
    old_order_attr = self.__dict__.get("_order")
    bound_order = self._order
    existed_sim = "_simulate" in self.__dict__
    old_sim_attr = self.__dict__.get("_simulate")
    bound_sim = self._simulate
    existed_balance = "native_balance_lamports" in self.__dict__
    old_balance_attr = self.__dict__.get("native_balance_lamports")
    bound_balance = self.native_balance_lamports

    def order_timed(*args, **kwargs):
        start = time.perf_counter_ns()
        try:
            return bound_order(*args, **kwargs)
        finally:
            marks["order_start"] = start
            marks["order_end"] = time.perf_counter_ns()

    def simulate_timed(*args, **kwargs):
        start = time.perf_counter_ns()
        marks["simulate_start"] = start
        try:
            return bound_sim(*args, **kwargs)
        finally:
            marks["simulate_end"] = time.perf_counter_ns()

    def balance_timed(*args, **kwargs):
        start = time.perf_counter_ns()
        try:
            return bound_balance(*args, **kwargs)
        finally:
            balances.append((start, time.perf_counter_ns()))

    self._order = order_timed
    self._simulate = simulate_timed
    self.native_balance_lamports = balance_timed
    result = None
    caught = None
    try:
        result = _ORIG_INNER_SWAP(self, input_mint, output_mint, amount_raw)
        return result
    except Exception as exc:
        caught = exc
        raise
    finally:
        total_end = time.perf_counter_ns()
        _restore_instance_attr(self, "_order", existed_order, old_order_attr)
        _restore_instance_attr(self, "_simulate", existed_sim, old_sim_attr)
        _restore_instance_attr(self, "native_balance_lamports", existed_balance, old_balance_attr)

        order_start = marks.get("order_start")
        order_end = marks.get("order_end")
        sim_start = marks.get("simulate_start")
        sim_end = marks.get("simulate_end")
        pre_balance = balances[0] if balances else None
        post_balance = balances[1] if len(balances) > 1 else None
        latency = {
            "pre_balance_ms": _ms(pre_balance[1] - pre_balance[0]) if pre_balance else None,
            "order_ms": _ms(int(order_end) - int(order_start)) if order_start and order_end else None,
            "transaction_construction_ms": _ms(int(sim_start) - int(order_end)) if sim_start and order_end else None,
            "simulation_ms": _ms(int(sim_end) - int(sim_start)) if sim_start and sim_end else None,
            "execute_ms": _ms(post_balance[0] - int(sim_end)) if post_balance and sim_end else None,
            "post_balance_ms": _ms(post_balance[1] - post_balance[0]) if post_balance else None,
            "execution_total_ms": _ms(total_end - total_start),
        }
        if isinstance(result, dict):
            result.setdefault("latency_ms", {}).update(latency)
        elif caught is not None:
            payload = getattr(caught, "result", None)
            if isinstance(payload, dict):
                payload.setdefault("latency_ms", {}).update(latency)


def update_attempt_timed(app, key, status, trade=None, error=""):
    result = _ORIG_UPDATE(app, key, status, trade, error)
    with _LOCK:
        context = _PENDING.pop(str(key), {})
    latency = dict((trade or {}).get("latency_ms") or {}) if isinstance(trade, dict) else {}
    if context:
        _ledger.record_sample(
            app,
            attempt_key=str(key),
            telegram_id=str(context.get("telegram_id") or ""),
            action=str(context.get("action") or ""),
            status=str(status),
            receive_delay_ms=context.get("receive_delay_ms"),
            strategy_ms=context.get("strategy_ms"),
            latency=latency,
            error=str(error or ""),
        )
    return result


def install():
    if getattr(_sol, "_execution_latency_ms_installed", False):
        return
    _sol._record_leader_event = record_leader_event_timed
    _live._claim_attempt = claim_attempt_timed
    _live._update_attempt = update_attempt_timed
    _eff._PREV_SWAP = timed_inner_swap
    _sol._execution_latency_ms_installed = True
    print("[execution-latency] high-resolution Solana LIVE stage telemetry enabled")


install()
