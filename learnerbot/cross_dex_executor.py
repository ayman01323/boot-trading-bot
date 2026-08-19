from __future__ import annotations

import csv
import json
import os
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from .auto_trader import _append, _append_simulation, _required_pre_fee_min, _user_exec_config, auto_state
from .config import load_kv_scoped
from .fee_engine import ledger, master_wallet, profit_share_amount, user_fee_plan
from .live_executor import ERC20_ABI, LiveTrader, LiveTradingError
from .multi_wallet_store import MultiWalletStore
from .product_universe import route_product_policy
from .user_registry import all_users, require_user

ATOMIC_V2_ABI = [
    {
        "type": "function", "name": "execute", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "routers", "type": "address[]"},
            {"name": "paths", "type": "address[][]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amountReturned", "type": "uint256"}],
    },
    {
        "type": "function", "name": "allowedRouter", "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function", "name": "allowedCaller", "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "bool"}],
    },
]

V2_QUOTE_ABI = [{
    "type": "function", "name": "getAmountsOut", "stateMutability": "view",
    "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
}]

def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}

def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(default)

def _rows(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def _executor_address(app, chain_id: int) -> str | None:
    cfg = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", chain_id)
    raw = str(cfg.get("cross_dex_atomic_executor_address") or "").strip()
    if not raw or not Web3.is_address(raw):
        return None
    return Web3.to_checksum_address(raw)

def _venue_routers(row: dict) -> list[str]:
    out = []
    for part in str(row.get("venue_plan") or "").split(";"):
        if ":" not in part:
            continue
        addr = part.rsplit(":", 1)[-1].strip()
        if Web3.is_address(addr):
            out.append(Web3.to_checksum_address(addr))
    return out[:2]

def _candidate_sizes(cfg: dict, platform: dict) -> list[Decimal]:
    cap = min(_dec(cfg.get("input"), "0.005"), _dec(cfg.get("max_input"), "0.05"))
    raw = str(platform.get("adaptive_size_multipliers") or "0.5,1,2,4").replace(";", ",")
    vals = []
    for x in raw.split(","):
        try:
            m = Decimal(x.strip())
        except Exception:
            continue
        if m <= 0:
            continue
        size = min(cap, _dec(cfg.get("input"), "0.005") * m)
        if size > 0 and size not in vals:
            vals.append(size)
    if cap > 0 and cap not in vals:
        vals.append(cap)
    return sorted(vals)

def _quote_round_trip(trader: LiveTrader, routers: list[str], token: str, amount: Decimal) -> dict:
    wrapped = trader.wrapped
    token = Web3.to_checksum_address(token)
    raw = int(amount * Decimal(10**18))
    r0 = trader.w3.eth.contract(address=routers[0], abi=V2_QUOTE_ABI)
    r1 = trader.w3.eth.contract(address=routers[1], abi=V2_QUOTE_ABI)
    mid = int(r0.functions.getAmountsOut(raw, [wrapped, token]).call()[-1])
    out = int(r1.functions.getAmountsOut(mid, [token, wrapped]).call()[-1])
    return {
        "amount": amount, "amount_raw": raw, "mid_raw": mid, "out_raw": out,
        "gross": Decimal(out - raw) / Decimal(10**18),
        "paths": [[wrapped, token], [token, wrapped]],
    }

def _private_rpc_request(url: str, method: str, params, auth_token: str | None = None):
    body = json.dumps({"jsonrpc": "2.0", "id": int(time.time() * 1000) % 2_000_000_000,
                       "method": method, "params": params}).encode()
    headers = {"content-type": "application/json", "user-agent": "BOOT-cross-dex/2.3.4"}
    if auth_token:
        headers["Authorization"] = auth_token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise LiveTradingError(f"{method} private RPC rejected: {data['error']}")
    return data.get("result")

def _submit_private_bsc(trader: LiveTrader, raw_hex: str) -> tuple[str, list[str]]:
    cfg = load_kv_scoped(Path(trader.app.csv_dir) / "auto_trading_settings.csv", trader.chain.chain_id)
    accepted = []
    errors = []
    block = int(trader.w3.eth.block_number)

    br = str(os.environ.get("BLOCKRAZOR_BSC_RPC_URL") or cfg.get("blockrazor_private_rpc_url") or "").strip()
    if br:
        try:
            _private_rpc_request(
                br, "eth_sendPrivateTransaction", [raw_hex],
                str(os.environ.get("BLOCKRAZOR_AUTH_TOKEN") or cfg.get("blockrazor_auth_token") or "").strip() or None,
            )
            accepted.append("blockrazor")
        except Exception as exc:
            errors.append(f"blockrazor:{type(exc).__name__}:{exc}")

    pu = str(os.environ.get("PUISSANT_BSC_RPC_URL") or cfg.get("puissant_builder_url") or "https://puissant-builder.48.club/").strip()
    if pu:
        try:
            _private_rpc_request(
                pu, "eth_sendBundle",
                [{"txs": [raw_hex], "maxBlockNumber": block + max(1, int(cfg.get("private_bundle_max_blocks") or 3))}],
            )
            accepted.append("puissant")
        except Exception as exc:
            errors.append(f"puissant:{type(exc).__name__}:{exc}")

    if not accepted:
        raise LiveTradingError("No private builder accepted transaction: " + " | ".join(errors[-3:]))
    return Web3.keccak(hexstr=raw_hex).hex(), accepted

def _sign_and_submit(trader: LiveTrader, tx: dict) -> tuple[str, list[str]]:
    if trader.account is None:
        raise LiveTradingError("Signing wallet unavailable")
    signed = trader.account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    raw_hex = raw.hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex

    cfg = load_kv_scoped(Path(trader.app.csv_dir) / "auto_trading_settings.csv", trader.chain.chain_id)
    require_private = _bool(cfg.get("private_submission_required", "true"), True)
    if trader.chain.chain_id == 56 and require_private:
        return _submit_private_bsc(trader, raw_hex)

    if require_private:
        raise LiveTradingError("Private submission is required but no configured private route exists for this chain")
    txh = trader.w3.eth.send_raw_transaction(raw)
    return txh.hex(), ["public_rpc_explicit_fallback"]

def _best_cross_candidate(trader: LiveTrader, row: dict, cfg: dict, platform: dict, pre_fee_min: Decimal):
    routers = _venue_routers(row)
    path = [x for x in str(row.get("route_path") or "").split(">") if Web3.is_address(x)]
    if len(routers) != 2 or len(path) != 3:
        return None
    token = Web3.to_checksum_address(path[1])
    executor = _executor_address(trader.app, trader.chain.chain_id)
    if not executor:
        return None
    ec = trader.w3.eth.contract(address=executor, abi=ATOMIC_V2_ABI)
    if not trader.w3.eth.get_code(executor):
        return None
    if not bool(ec.functions.allowedCaller(trader.address).call()):
        return None
    if not all(bool(ec.functions.allowedRouter(r).call()) for r in routers):
        return None

    wrapped_c = trader.w3.eth.contract(address=trader.wrapped, abi=ERC20_ABI)
    balance = int(wrapped_c.functions.balanceOf(trader.address).call())
    allowance = int(wrapped_c.functions.allowance(trader.address, executor).call())
    best = None

    rough_gas_units = max(180000, min(900000, int(platform.get("cross_dex_gas_units_estimate") or 350000)))
    fee_tx = trader._base_tx()
    fee_per_gas = int(fee_tx.get("maxFeePerGas") or fee_tx.get("gasPrice") or trader.w3.eth.gas_price)
    rough_gas = Decimal(int(rough_gas_units * 1.30) * fee_per_gas) / Decimal(10**18)

    for amount in _candidate_sizes(cfg, platform):
        try:
            q = _quote_round_trip(trader, routers, token, amount)
        except Exception:
            continue
        if balance < q["amount_raw"] or allowance < q["amount_raw"]:
            continue
        expected_net = q["gross"] - rough_gas
        if expected_net < pre_fee_min:
            continue
        score = expected_net
        if best is None or score > best["expected_net"]:
            best = {**q, "routers": routers, "executor": executor, "expected_net": expected_net}
    return best

def _preflight(trader: LiveTrader, cand: dict, pre_fee_min: Decimal) -> tuple[dict, dict]:
    ec = trader.w3.eth.contract(address=cand["executor"], abi=ATOMIC_V2_ABI)
    deadline = trader._deadline()
    amount_raw = int(cand["amount_raw"])
    provisional_min = int(max(Decimal(0), pre_fee_min) * Decimal(10**18))
    fn0 = ec.functions.execute(cand["routers"], cand["paths"], amount_raw, provisional_min, deadline)
    tx0 = trader._base_tx()
    try:
        gas0 = int(fn0.estimate_gas(tx0))
    except Exception as exc:
        raise LiveTradingError(f"cross-DEX executor simulation failed: {exc}") from exc

    fee_per_gas = int(tx0.get("maxFeePerGas") or tx0.get("gasPrice") or trader.w3.eth.gas_price)
    gas_units = int(gas0 * 1.30)
    gas_cost = Decimal(gas_units * fee_per_gas) / Decimal(10**18)
    min_profit = pre_fee_min + gas_cost
    min_profit_raw = int(min_profit * Decimal(10**18))
    if cand["gross"] <= min_profit:
        raise LiveTradingError("cross-DEX quoted gross edge does not cover conservative gas reserve plus minimum net")

    fn = ec.functions.execute(cand["routers"], cand["paths"], amount_raw, min_profit_raw, deadline)
    tx = trader._base_tx()
    try:
        gas = int(fn.estimate_gas(tx))
    except Exception as exc:
        raise LiveTradingError(f"profit-protected cross-DEX simulation failed: {exc}") from exc
    if gas > gas0:
        gas_units = int(gas * 1.30)
        gas_cost = Decimal(gas_units * fee_per_gas) / Decimal(10**18)
        min_profit = pre_fee_min + gas_cost
        min_profit_raw = int(min_profit * Decimal(10**18))
        if cand["gross"] <= min_profit:
            raise LiveTradingError("re-estimated cross-DEX gas removes the profit edge")
        fn = ec.functions.execute(cand["routers"], cand["paths"], amount_raw, min_profit_raw, deadline)
        gas = int(fn.estimate_gas(trader._base_tx()))

    tx = trader._base_tx()
    tx["gas"] = max(180000, int(gas * 1.15))
    built = fn.build_transaction(tx)
    keys = {"from", "to", "gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas",
            "value", "data", "accessList", "type"}
    trader.w3.eth.call({k: v for k, v in built.items() if k in keys})
    return {
        **cand, "simulation_ok": True, "gas": gas, "gas_cost_base": gas_cost,
        "min_profit_base": min_profit, "reason": "PASS",
    }, built

def execute_best_cross_dex_opportunity(app, opportunities: list[dict]) -> list[dict]:
    platform = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    if not _bool(platform.get("auto_trading_enabled", "false"), False):
        return []
    if not _bool(platform.get("cross_dex_live_enabled", "false"), False):
        return []

    cross = [r for r in opportunities
             if str(r.get("route_kind") or "").upper() == "CROSS_DEX_V2"
             and _bool(r.get("scanner_exact"), False)
             and _bool(r.get("source_verified"), False)
             and _bool(r.get("exact_quote_ok"), False)]
    if not cross:
        return []

    store = MultiWalletStore(app.data_dir, app.csv_dir)
    events = []
    now = int(time.time())

    for u in all_users(app.csv_dir, enabled_only=True):
        if (u.get("status") or "").upper() != "ACTIVE" or not _bool(u.get("can_auto_trade"), True):
            continue
        tid = str(u.get("telegram_id") or "").strip()
        if not tid or not store.has_wallet(tid):
            continue
        try:
            meta = store.get_meta(tid)
        except Exception:
            continue

        best = None
        for r in cross:
            slug = str(r.get("chain_slug") or "").strip().lower()
            if not slug:
                continue
            try:
                require_user(app.csv_dir, tid, active=True, chain_slug=slug)
                trader = LiveTrader(app, slug, telegram_id=tid, wallet_id=meta["wallet_id"])
            except Exception:
                continue
            cfg = _user_exec_config(app, tid, trader.chain.chain_id)
            if not (cfg["auto_on"] and cfg["live_on"] and cfg["mode"] == "ARMED"):
                continue
            state = auto_state(app.csv_dir, tid)
            if state["hour_trades"] >= cfg["max_hour"] or state["hour_expected_gas"] >= cfg["max_gas_hour"]:
                continue
            if now - state["last_ts"] < cfg["cooldown"]:
                continue
            plan = user_fee_plan(app.csv_dir, tid) or {}
            if _dec(plan.get("profit_share_bps") or 0) > 0 and not master_wallet(app.csv_dir, trader.chain.chain_id):
                continue
            pre_fee_min = _required_pre_fee_min(plan, cfg["min_user_net"])

            path = [x for x in str(r.get("route_path") or "").split(">") if x]
            policy = route_product_policy(app.csv_dir, trader.chain.chain_id, path)
            if not bool(policy.get("auto_trade")):
                continue
            cand = _best_cross_candidate(trader, r, cfg, platform, pre_fee_min)
            if not cand:
                continue
            if best is None or cand["expected_net"] > best["cand"]["expected_net"]:
                best = {"row": r, "cand": cand, "trader": trader, "plan": plan,
                        "cfg": cfg, "pre_fee_min": pre_fee_min}

        if not best:
            continue

        r = best["row"]; cand = best["cand"]; trader = best["trader"]; plan = best["plan"]
        try:
            sim, built = _preflight(trader, cand, best["pre_fee_min"])
            _append_simulation(app.csv_dir, {
                "timestamp_epoch": int(time.time()), "telegram_id": tid, "wallet_id": meta.get("wallet_id", ""),
                "chain_id": trader.chain.chain_id, "chain_slug": trader.chain.slug, "route_id": r.get("route_id", ""),
                "route_path": r.get("route_path", ""), "input_base": str(cand["amount"]),
                "min_net_profit_base": str(best["pre_fee_min"]), "gross_profit_base": str(cand["gross"]),
                "gas_cost_base": str(sim["gas_cost_base"]), "simulation_ok": "true", "reason": "PASS_CROSS_DEX",
            })
        except Exception as exc:
            _append_simulation(app.csv_dir, {
                "timestamp_epoch": int(time.time()), "telegram_id": tid, "wallet_id": meta.get("wallet_id", ""),
                "chain_id": getattr(trader.chain, "chain_id", ""), "chain_slug": getattr(trader.chain, "slug", ""),
                "route_id": r.get("route_id", ""), "route_path": r.get("route_path", ""),
                "input_base": str(cand.get("amount", "")), "min_net_profit_base": str(best["pre_fee_min"]),
                "gross_profit_base": str(cand.get("gross", "")), "gas_cost_base": "",
                "simulation_ok": "false", "reason": f"{type(exc).__name__}: {str(exc)[:450]}",
            })
            continue

        before = trader.wrapped_balance()
        try:
            txh, providers = _sign_and_submit(trader, built)
            receipt = trader.w3.eth.wait_for_transaction_receipt(txh, timeout=180, poll_latency=2)
            if int(receipt.status) != 1:
                raise LiveTradingError(f"cross-DEX transaction failed: {txh}")
            after = trader.wrapped_balance()
            gp = int(getattr(receipt, "effectiveGasPrice", 0) or receipt.get("effectiveGasPrice", 0) or 0)
            gu = int(getattr(receipt, "gasUsed", 0) or receipt.get("gasUsed", 0) or 0)
            actual_gas = Decimal(gp * gu) / Decimal(10**18)
            realised = (after - before) - actual_gas
            fee = profit_share_amount(app.csv_dir, tid, realised)
            fee_tx = ""
            fee_status = "NONE"
            master = master_wallet(app.csv_dir, trader.chain.chain_id)
            if fee > 0 and master:
                try:
                    fee_tx = trader.transfer_wrapped_raw(master, int(fee * Decimal(10**18)))
                    fee_status = "BROADCAST"
                    ledger(app.csv_dir, {
                        "telegram_id": tid, "wallet_id": meta["wallet_id"], "chain_id": trader.chain.chain_id,
                        "fee_type": "PROFIT_SHARE", "plan_id": u.get("fee_plan_id") or "",
                        "gross_profit_base": str(after - before), "gas_cost_base": str(actual_gas),
                        "net_profit_base": str(realised), "fee_amount_base": str(fee),
                        "fee_asset": trader.chain.wrapped_base_symbol, "master_address": master,
                        "tx_hash": fee_tx, "status": "BROADCAST", "note": "Atomic cross-DEX realised-profit fee",
                    })
                except Exception:
                    fee_status = "PENDING"

            status = "SUCCESS_FEE_PENDING" if fee_status == "PENDING" else "SUCCESS"
            row = {
                "timestamp_epoch": now, "telegram_id": tid, "wallet_id": meta["wallet_id"],
                "chain_id": trader.chain.chain_id, "chain_slug": trader.chain.slug,
                "route_id": r.get("route_id"), "route_path": r.get("route_path"),
                "input_base": str(cand["amount"]), "expected_gross_base": str(cand["gross"]),
                "expected_gas_base": str(sim["gas_cost_base"]),
                "expected_net_base": str(cand["gross"] - sim["gas_cost_base"]),
                "realised_net_base": str(realised), "profit_fee_base": str(fee),
                "fee_tx_hash": fee_tx, "tx_hash": txh, "status": status,
                "note": "atomic_cross_dex|adaptive_size|private=" + ",".join(providers),
            }
            _append(Path(app.csv_dir) / "auto" / "auto_trade_execution.csv", row)
            events.append(row)
        except Exception as exc:
            row = {
                "timestamp_epoch": now, "telegram_id": tid, "wallet_id": meta["wallet_id"],
                "chain_id": trader.chain.chain_id, "chain_slug": trader.chain.slug,
                "route_id": r.get("route_id"), "route_path": r.get("route_path"),
                "input_base": str(cand["amount"]), "expected_gross_base": str(cand["gross"]),
                "expected_gas_base": str(sim.get("gas_cost_base", "")),
                "expected_net_base": str(cand["gross"] - sim.get("gas_cost_base", Decimal(0))),
                "realised_net_base": "", "profit_fee_base": "", "fee_tx_hash": "",
                "tx_hash": "", "status": "FAILED",
                "note": f"atomic_cross_dex:{type(exc).__name__}:{str(exc)[:300]}",
            }
            _append(Path(app.csv_dir) / "auto" / "auto_trade_execution.csv", row)
            events.append(row)

    return events
