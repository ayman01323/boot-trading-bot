from __future__ import annotations

import base64
import json
import math
import threading
import time
from pathlib import Path

import requests

from .csvio import as_bool, read_rows

COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
COINGECKO_SOL = "https://api.coingecko.com/api/v3/simple/price"

_INSTALLED = False
_CTX = threading.local()
_SOL_PRICE_CACHE = [0.0, 0.0]
_TX_FEE_CACHE: dict[str, int] = {}


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _rt(settings, key: str, default=None):
    value = settings.runtime().get(key)
    return default if value is None or str(value).strip() == "" else value


def sol_usd(settings) -> float:
    now = time.time()
    if _SOL_PRICE_CACHE[1] > 0 and now - _SOL_PRICE_CACHE[0] < 60:
        return _SOL_PRICE_CACHE[1]
    try:
        r = requests.get(
            COINGECKO_SOL,
            params={"ids": "solana", "vs_currencies": "usd"},
            headers={"User-Agent": "SiRisky/0.4-one-usd-safety"},
            timeout=6,
        )
        r.raise_for_status()
        price = _num(((r.json() or {}).get("solana") or {}).get("usd"), 0)
        if 20 <= price <= 1000:
            _SOL_PRICE_CACHE[:] = [now, price]
            return price
    except Exception:
        pass
    fallback = _num(_rt(settings, "sol_usd_fallback", 108.0), 108.0)
    if not 20 <= fallback <= 1000:
        fallback = 108.0
    return fallback


def entry_sol(settings) -> float:
    usd = max(0.01, _num(_rt(settings, "auto_entry_usd", 1.0), 1.0))
    amount = usd / sol_usd(settings)
    minimum = max(0.0001, _num(_rt(settings, "auto_entry_min_sol", 0.001), 0.001))
    maximum = max(minimum, _num(_rt(settings, "auto_entry_max_sol", 0.02), 0.02))
    return min(maximum, max(minimum, amount))


def inspect_order_network_fee(order_data: dict) -> dict:
    """Estimate signature + compute-budget priority fee from Jupiter transaction."""
    result = {"signature_fee_lamports": 5000, "priority_fee_lamports": 0, "estimated_network_fee_lamports": 5000,
              "compute_unit_limit": 0, "compute_unit_price_micro_lamports": 0}
    try:
        from solders.pubkey import Pubkey
        from solders.transaction import VersionedTransaction

        raw = base64.b64decode(order_data["transaction"], validate=True)
        tx = VersionedTransaction.from_bytes(raw)
        message = tx.message
        result["signature_fee_lamports"] = max(5000, int(message.header.num_required_signatures) * 5000)
        keys = list(message.account_keys)
        compute = Pubkey.from_string(COMPUTE_BUDGET_PROGRAM)
        limit = 200_000
        micro = 0
        for ix in message.instructions:
            try:
                if int(ix.program_id_index) >= len(keys) or keys[int(ix.program_id_index)] != compute:
                    continue
                data = bytes(ix.data)
                if len(data) >= 5 and data[0] == 2:
                    limit = int.from_bytes(data[1:5], "little")
                elif len(data) >= 9 and data[0] == 3:
                    micro = int.from_bytes(data[1:9], "little")
            except Exception:
                continue
        priority = int(math.ceil(limit * micro / 1_000_000.0)) if micro > 0 else 0
        for key in ("prioritizationFeeLamports", "priorityFeeLamports"):
            priority = max(priority, _int(order_data.get(key), 0))
        result.update({"priority_fee_lamports": priority, "compute_unit_limit": limit,
                       "compute_unit_price_micro_lamports": micro,
                       "estimated_network_fee_lamports": result["signature_fee_lamports"] + priority})
    except Exception:
        pass
    return result


def _open_entry_lamports(settings, mint: str) -> int:
    try:
        for row in reversed(read_rows(settings.csv_dir / "open_positions.csv")):
            if str(row.get("mint") or "") == mint and str(row.get("status") or "OPEN").upper() == "OPEN":
                return _int(row.get("entry_lamports"), 0)
    except Exception:
        pass
    return 0


def _gate_order(settings, order_data: dict, input_mint: str, output_mint: str, amount_raw: int) -> dict:
    fee = inspect_order_network_fee(order_data)
    priority_cap = max(0, _int(_rt(settings, "max_priority_fee_lamports", 30000), 30000))
    if fee["priority_fee_lamports"] > priority_cap:
        raise RuntimeError(f"PRIORITY_FEE_CAP:{fee['priority_fee_lamports']}>{priority_cap}")

    from .jupiter import WSOL_MINT
    is_buy = input_mint == WSOL_MINT
    basis = int(amount_raw) if is_buy else _int(getattr(_CTX, "entry_lamports", 0), 0)
    if basis <= 0 and not is_buy:
        basis = _open_entry_lamports(settings, output_mint if output_mint != WSOL_MINT else str(getattr(_CTX, "mint", "")))
    normal_pct = _num(_rt(settings, "max_buy_network_fee_pct" if is_buy else "max_sell_network_fee_pct", 1.0), 1.0)
    emergency_reasons = {"FAST_STOP", "FAST_STOP_NET", "EXIT_HEALTH", "HOT_REVERSAL"}
    if not is_buy and str(getattr(_CTX, "reason", "")) in emergency_reasons:
        normal_pct = max(normal_pct, _num(_rt(settings, "max_emergency_sell_network_fee_pct", 2.0), 2.0))
    if basis > 0:
        pct = fee["estimated_network_fee_lamports"] / basis * 100.0
        if pct > normal_pct:
            raise RuntimeError(f"NETWORK_FEE_PCT_CAP:{pct:.3f}>{normal_pct:.3f}")
    order_data["_sirisky_fee_estimate"] = fee
    return order_data


def _rpc(settings, method: str, params: list):
    rpc = settings.resolve_rpc("http")
    r = requests.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def transaction_fee_lamports(settings, signature: str) -> int:
    if not signature:
        return 0
    if signature in _TX_FEE_CACHE:
        return _TX_FEE_CACHE[signature]
    try:
        result = _rpc(settings, "getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}]) or {}
        fee = _int((result.get("meta") or {}).get("fee"), 0)
        if fee > 0:
            _TX_FEE_CACHE[signature] = fee
        return fee
    except Exception:
        return 0


def zero_token_accounts(settings, mint: str | None = None) -> list[dict]:
    from .wallet import WalletStore
    owner = WalletStore(settings).address()
    selector = {"mint": mint} if mint else {"programId": TOKEN_PROGRAM}
    result = _rpc(settings, "getTokenAccountsByOwner", [owner, selector, {"encoding": "jsonParsed", "commitment": "confirmed"}]) or {}
    rows = []
    for item in result.get("value") or []:
        account = item.get("account") or {}
        info = (((account.get("data") or {}).get("parsed") or {}).get("info") or {})
        amount = str(((info.get("tokenAmount") or {}).get("amount")) or "0")
        close_authority = str(info.get("closeAuthority") or owner)
        if amount == "0" and str(info.get("owner") or "") == owner and close_authority == owner:
            rows.append({"pubkey": str(item.get("pubkey") or ""), "mint": str(info.get("mint") or ""),
                         "lamports": _int(account.get("lamports"), 0)})
    return [r for r in rows if r["pubkey"]]


def close_zero_token_accounts(settings, mint: str | None = None, broadcast: bool = True) -> dict:
    """Close only wallet-owned, zero-balance classic SPL token accounts."""
    rows = zero_token_accounts(settings, mint)
    if not rows:
        return {"status": "NONE", "count": 0, "recoverable_lamports": 0, "signature": ""}
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction
    from .wallet import WalletStore

    store = WalletStore(settings)
    kp_bytes = store.keypair_bytes()
    from solders.keypair import Keypair
    kp = Keypair.from_bytes(bytes(kp_bytes))
    owner = kp.pubkey()
    program = Pubkey.from_string(TOKEN_PROGRAM)
    ixs = []
    for row in rows[:12]:
        account = Pubkey.from_string(row["pubkey"])
        ixs.append(Instruction(program, bytes([9]), [AccountMeta(account, False, True), AccountMeta(owner, False, True), AccountMeta(owner, True, False)]))
    latest = _rpc(settings, "getLatestBlockhash", [{"commitment": "confirmed"}]) or {}
    blockhash = Hash.from_string(str((latest.get("value") or {}).get("blockhash") or ""))
    message = Message.new_with_blockhash(ixs, owner, blockhash)
    tx = Transaction.new_unsigned(message)
    tx.sign([kp], blockhash)
    encoded = base64.b64encode(bytes(tx)).decode("ascii")
    sim = _rpc(settings, "simulateTransaction", [encoded, {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}]) or {}
    if (sim.get("value") or {}).get("err") is not None:
        raise RuntimeError("ZERO_ACCOUNT_CLOSE_SIMULATION_FAILED")
    if not broadcast:
        return {"status": "SIMULATED", "count": len(ixs), "recoverable_lamports": sum(r["lamports"] for r in rows[:12]), "signature": ""}
    signature = str(_rpc(settings, "sendTransaction", [encoded, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 3}]) or "")
    if not signature:
        raise RuntimeError("ZERO_ACCOUNT_CLOSE_BROADCAST_FAILED")
    for _ in range(20):
        status = _rpc(settings, "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]) or {}
        value = ((status.get("value") or [None])[0])
        if value and value.get("err") is not None:
            raise RuntimeError("ZERO_ACCOUNT_CLOSE_FAILED")
        if value and str(value.get("confirmationStatus") or "") in {"confirmed", "finalized"}:
            break
        time.sleep(0.5)
    return {"status": "CLOSED", "count": len(ixs), "recoverable_lamports": sum(r["lamports"] for r in rows[:12]), "signature": signature}


def install_safety_v2() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import stage5_trade as s5mod
    from .engine import SiRiskyEngine
    from .stage5_trade import Stage5Trade
    from .stage6_monitor import Stage6Monitor
    from .jupiter import order as raw_jupiter_order

    original_eval_pool = SiRiskyEngine._evaluate_pool_for_entry
    def safe_eval_pool(self, pool, discovery):
        patched = dict(pool)
        patched["probe_sol"] = f"{entry_sol(self.settings):.9f}"
        return original_eval_pool(self, patched, discovery)
    SiRiskyEngine._evaluate_pool_for_entry = safe_eval_pool

    original_stage5_execute = Stage5Trade.execute
    def stage5_execute(self, order):
        _CTX.action = str(order.action or "")
        _CTX.reason = str(order.reason or "")
        _CTX.mint = str(order.mint or "")
        _CTX.entry_lamports = int(order.amount_raw) if _CTX.action == "BUY" else _open_entry_lamports(self.settings, _CTX.mint)
        try:
            return original_stage5_execute(self, order)
        finally:
            _CTX.action = _CTX.reason = _CTX.mint = ""
            _CTX.entry_lamports = 0
    Stage5Trade.execute = stage5_execute

    def safe_jupiter_order(settings, taker, input_mint, output_mint, amount_raw, timeout=30):
        data = raw_jupiter_order(settings, taker, input_mint, output_mint, amount_raw, timeout=timeout)
        return _gate_order(settings, data, input_mint, output_mint, amount_raw)
    s5mod.jup_order = safe_jupiter_order

    original_monitor_eval = Stage6Monitor.evaluate
    def fee_aware_monitor(self, position: dict):
        ev = original_monitor_eval(self, position)
        if str(position.get("mode") or "").upper() != "LIVE" or _int(ev.get("sell_lamports"), 0) <= 0:
            return ev
        entry = max(1, _int(position.get("entry_lamports"), 1))
        sell = _int(ev.get("sell_lamports"), 0)
        buy_fee = transaction_fee_lamports(self.settings, str(position.get("buy_signature") or ""))
        if buy_fee <= 0:
            buy_fee = max(5000, _int(_rt(self.settings, "estimated_buy_network_fee_lamports", 35000), 35000))
        sell_fee = max(5000, _int(_rt(self.settings, "estimated_sell_network_fee_lamports", 35000), 35000))
        gross_pct = (sell - entry) / entry * 100.0
        true_net_pct = (sell - entry - buy_fee - sell_fee) / entry * 100.0
        ev["gross_net_pct"] = gross_pct
        ev["net_pct"] = true_net_pct
        ev["buy_network_fee_lamports"] = buy_fee
        ev["estimated_sell_network_fee_lamports"] = sell_fee
        ev["estimated_round_trip_network_fee_pct"] = (buy_fee + sell_fee) / entry * 100.0
        target = _num(ev.get("dynamic_target_net_pct"), 2.0)
        age = _int(ev.get("time_in_trade_sec"), 0)
        fast_stop = -abs(_num(self.settings.risk().get("fast_stop_net_pct"), 3.0))
        max_hold = max(1, min(_int(position.get("max_hold_seconds"), 90), _int(self.settings.risk().get("fast_max_hold_cap_seconds"), 90)))

        if ev.get("reason") == "NO_TOKEN_BALANCE":
            return ev
        if _num(ev.get("exit_health_pct"), 100.0) < _num(self.settings.risk().get("min_exit_health_pct"), 98.0):
            ev.update(decision="EXIT", reason="EXIT_HEALTH", temperature="HOT")
            return ev
        if true_net_pct <= fast_stop:
            ev.update(decision="EXIT", reason="FAST_STOP_NET", temperature="HOT")
            return ev
        if ev.get("reason") in {"HOT_REVERSAL", "WARM_REVERSAL"}:
            return ev
        if true_net_pct >= target:
            ev.update(decision="EXIT", reason="FAST_TAKE_PROFIT_NET", temperature="WARM")
            return ev
        momentum_at = max(10, _int(_rt(self.settings, "momentum_check_seconds", 30), 30))
        momentum_min = _num(_rt(self.settings, "momentum_min_net_pct", 0.0), 0.0)
        if age >= momentum_at and true_net_pct <= momentum_min:
            ev.update(decision="EXIT", reason="MOMENTUM_FAILURE", temperature="WARM")
            return ev
        if age >= max_hold:
            ev.update(decision="EXIT", reason="MAX_HOLD_TIME", temperature="HOT")
            return ev
        ev.update(decision="HOLD", reason="NET_TARGET_NOT_MET")
        return ev
    Stage6Monitor.evaluate = fee_aware_monitor

    original_monitor_cycle = SiRiskyEngine.monitor_cycle
    def safe_monitor_cycle(self):
        before = self.open_positions()
        result = original_monitor_cycle(self)
        if result.get("status") == "CLOSED" and before and str(before[0].get("mode") or "").upper() == "LIVE":
            if as_bool(_rt(self.settings, "auto_cleanup_zero_token_accounts", "1"), True):
                try:
                    result["token_account_cleanup"] = close_zero_token_accounts(self.settings, str(before[0].get("mint") or ""), broadcast=True)
                except Exception as exc:
                    result["token_account_cleanup"] = {"status": "FAILED", "error": type(exc).__name__}
        return result
    SiRiskyEngine.monitor_cycle = safe_monitor_cycle
