from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation

from . import sibot1_live_bridge_patch as _bridge
from .live_executor import LiveTrader

_PREV_PROCESS = _bridge._process_candidate
MIN_NET_EDGE_BPS = Decimal("12")


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def _route(candidate) -> list[str]:
    return [x.strip() for x in str(candidate.get("route_path") or "").split(">") if x.strip()]


def _execute_atomic_cycle(app, tid, candidate, key):
    if str(candidate.get("engine_id") or "").lower() != "gpt":
        raise RuntimeError("atomic Base LIVE cycle is restricted to the GPT SiBot 1 engine")
    if str(candidate.get("poolcheck_verdict") or "").upper() != "PASS":
        raise RuntimeError("LIVE atomic cycle requires central PoolCheck PASS")

    route_kind = str(candidate.get("route_kind") or "").upper()
    if route_kind not in {"V2_CYCLE", "V3_CYCLE"}:
        raise RuntimeError(f"unsupported atomic LIVE route kind: {route_kind or 'missing'}")
    if str(candidate.get("execution_mode") or "").upper().startswith("SHADOW"):
        raise RuntimeError("source route is SHADOW-only")

    path = _route(candidate)
    if len(path) < 3 or path[0].lower() != path[-1].lower():
        raise RuntimeError("atomic LIVE route must be a closed cycle")

    ctl = _bridge.control(app, tid)
    amount = _bridge._fixed_entry_size(ctl)
    observed_edge = _dec(candidate.get("net_edge_bps"), "0")
    if observed_edge < MIN_NET_EDGE_BPS:
        raise RuntimeError(f"observed net edge {observed_edge} bps is below {MIN_NET_EDGE_BPS} bps")
    min_profit = amount * MIN_NET_EDGE_BPS / Decimal("10000")

    router = str(candidate.get("router_address") or "").strip()
    quoter = str(candidate.get("quoter_address") or "").strip()
    tx_hash = ""

    if route_kind == "V2_CYCLE":
        if not router:
            raise RuntimeError("V2 atomic cycle is missing its exact router address")
        trader = LiveTrader(app, "base", telegram_id=tid, router_override=router)
        # Selection-time wallet simulation, followed by execute_cycle's mandatory
        # second exact-transaction preflight/eth_call immediately before signing.
        pre = trader.preflight_cycle(path, amount, min_profit)
        if not pre.get("simulation_ok"):
            raise RuntimeError(str(pre.get("reason") or "V2 cycle preflight failed"))
        before = trader.wrapped_balance()
        result = trader.execute_cycle(path, amount, min_profit, "CONFIRM")
        tx_hash = str(result.get("tx_hash") or "")
    else:
        if not router or not quoter:
            raise RuntimeError("V3 atomic cycle is missing router/quoter metadata")
        fees = [int(x) for x in str(candidate.get("route_fees") or "").split(">") if str(x).strip()]
        if len(fees) != len(path) - 1:
            raise RuntimeError("V3 atomic cycle fee count does not match route hops")
        trader = LiveTrader(app, "base", telegram_id=tid)
        pre = trader.simulate_v3_cycle(path, fees, amount, min_profit, router, quoter)
        if not pre.get("simulation_ok"):
            raise RuntimeError(str(pre.get("reason") or "V3 cycle preflight failed"))
        before = trader.wrapped_balance()
        result = trader.execute_v3_cycle(path, fees, amount, min_profit, router, quoter, "CONFIRM")
        tx_hash = str(result.get("tx_hash") or "")

    if not tx_hash:
        raise RuntimeError("atomic cycle returned no transaction hash")
    _bridge._attempt_update(app, key, "BROADCAST", tx_hash)

    try:
        receipt = trader.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180, poll_latency=2)
    except Exception as exc:
        _bridge.set_control(app, tid, auto_enabled="false")
        raise RuntimeError(f"atomic cycle broadcast {tx_hash} but receipt timed out; SiBot 1 AUTO paused") from exc
    if int(receipt.status) != 1:
        _bridge.set_control(app, tid, auto_enabled="false")
        raise RuntimeError(f"atomic cycle failed on-chain: {tx_hash}; SiBot 1 AUTO paused")

    after = trader.wrapped_balance()
    gas_price = int(getattr(receipt, "effectiveGasPrice", 0) or receipt.get("effectiveGasPrice", 0) or 0)
    gas_used = int(getattr(receipt, "gasUsed", 0) or receipt.get("gasUsed", 0) or 0)
    actual_gas = Decimal(gas_price * gas_used) / Decimal(10**18)
    realised_net = (after - before) - actual_gas

    if realised_net <= 0:
        _bridge.set_control(app, tid, auto_enabled="false")
        _bridge._attempt_update(app, key, "EXECUTED_NONPROFIT_AUTO_PAUSED", tx_hash)
        _bridge._notify(
            app,
            tid,
            "⚠️ <b>SiBot 1 GPT Base atomic cycle confirmed but was not net-profitable</b>\n"
            f"Realised net: <b>{realised_net} ETH</b>\n"
            f"TX: <code>{html.escape(tx_hash)}</code>\n"
            "SiBot 1 AUTO entries were paused automatically.",
        )
        return

    _bridge._attempt_update(app, key, "EXECUTED", tx_hash)
    _bridge._notify(
        app,
        tid,
        "🚀 <b>SiBot 1 GPT Base ATOMIC CYCLE confirmed</b>\n"
        f"Route: <code>{html.escape(str(candidate.get('route_id') or ''))}</code>\n"
        f"Canary input: <b>{amount} ETH</b>\n"
        f"Realised net after gas: <b>{realised_net} ETH</b>\n"
        f"TX: <code>{html.escape(tx_hash)}</code>",
    )


def _process_candidate(app, tid, candidate):
    if str(candidate.get("kind") or "").upper() != "ARBITRAGE":
        return _PREV_PROCESS(app, tid, candidate)
    if str(candidate.get("chain") or "").lower() != "base":
        return
    if _bridge._candidate_age(candidate) > _bridge.MAX_SIGNAL_AGE_SECONDS:
        return

    ctl = _bridge.control(app, tid)
    if not (
        _bridge._bool(ctl.get("armed"))
        and _bridge._bool(ctl.get("live_enabled"))
        and _bridge._bool(ctl.get("auto_enabled"))
    ):
        return
    ready = _bridge.readiness(app, tid)
    if not ready.get("signer_ready") or not ready.get("platform", {}).get("ready"):
        return

    claimed, key = _bridge._claim(app, tid, candidate)
    if not claimed:
        return
    try:
        _execute_atomic_cycle(app, tid, candidate, key)
    except Exception as exc:
        _bridge._attempt_update(app, key, "REJECTED_OR_FAILED", error=f"{type(exc).__name__}: {exc}")
        _bridge._notify(
            app,
            tid,
            "🚨 <b>SiBot 1 GPT atomic LIVE candidate blocked</b>\n"
            f"<code>{html.escape(type(exc).__name__ + ': ' + str(exc)[:500])}</code>",
        )


def install() -> None:
    if getattr(_bridge, "_gpt_atomic_cycle_live_installed", False):
        return
    _bridge._process_candidate = _process_candidate
    _bridge._gpt_atomic_cycle_live_installed = True
    print("[sibot1-live-bridge] gpt-atomic-cycle=enabled cross-dex-live=false")


install()
