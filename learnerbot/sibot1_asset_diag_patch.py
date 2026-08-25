from __future__ import annotations

import csv
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from . import sibot1_runtime_diag_export_patch as _diag
from . import solana_sibot as _sol
from .config import load_chains, load_kv_scoped
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import user_setting

_PREV_SNAPSHOT = _diag.snapshot
_CACHE_LOCK = threading.Lock()
_CACHE = {"ts": 0.0, "data": {}}
CACHE_SECONDS = 60


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def _control_rows(app) -> list[dict]:
    path = Path(app.csv_dir) / "sibot1" / "live_control.csv"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []
    out = []
    seen = set()
    for row in rows:
        tid = str(row.get("telegram_id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(row)
    return out


def _rpc(url: str, method: str, params: list, timeout: float = 6.0):
    response = requests.post(
        str(url),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
        headers={"User-Agent": "BOOT-sibot1-redacted-asset-diag/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload.get("error"))[:180])
    return payload.get("result")


def _base_balance(app, rows: list[dict]) -> dict:
    result = {
        "symbol": "ETH",
        "chain": "base",
        "wallet_count": 0,
        "balance_native": "0",
        "reserved_native": "0",
        "usable_native": "0",
        "rpc_ok": False,
    }
    try:
        base = next(c for c in load_chains(app, enabled_only=True) if str(c.slug).lower() == "base")
    except Exception:
        result["error"] = "Base chain is not enabled/configured"
        return result

    live_cfg = load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", base.chain_id)
    fallback_reserve = str(live_cfg.get("min_native_gas_reserve") or "0")
    store = MultiWalletStore(app.data_dir, app.csv_dir)
    balances: list[Decimal] = []
    reserves: list[Decimal] = []
    rpc_urls = [str(x) for x in (getattr(base, "rpc_urls", None) or []) if str(x).strip()]
    last_error = ""

    for row in rows:
        tid = str(row.get("telegram_id") or "").strip()
        if not tid:
            continue
        try:
            meta = store.get_meta(tid)
            address = str(meta.get("address") or "").strip()
            if not address:
                continue
            raw = None
            for url in rpc_urls:
                try:
                    chain_hex = _rpc(url, "eth_chainId", [])
                    if int(str(chain_hex), 16) != int(base.chain_id):
                        continue
                    raw = _rpc(url, "eth_getBalance", [address, "latest"])
                    break
                except Exception as exc:
                    last_error = type(exc).__name__
            if raw is None:
                continue
            balance = Decimal(int(str(raw), 16)) / Decimal(10**18)
            reserve = max(
                Decimal(0),
                _dec(user_setting(app.csv_dir, tid, base.chain_id, "min_native_gas_reserve", fallback_reserve), fallback_reserve),
            )
            balances.append(balance)
            reserves.append(reserve)
        except Exception as exc:
            last_error = type(exc).__name__

    total = sum(balances, Decimal(0))
    reserve_total = sum(reserves, Decimal(0))
    usable = sum((max(Decimal(0), b - r) for b, r in zip(balances, reserves)), Decimal(0))
    result.update({
        "wallet_count": len(balances),
        "balance_native": str(total),
        "reserved_native": str(reserve_total),
        "usable_native": str(usable),
        "rpc_ok": bool(balances),
    })
    if not balances and last_error:
        result["error"] = last_error
    return result


def _solana_balance(app, rows: list[dict]) -> dict:
    result = {
        "symbol": "SOL",
        "chain": "solana",
        "wallet_count": 0,
        "balance_native": "0",
        "reserved_native": "0",
        "usable_native": "0",
        "configured_trade_native": "0",
        "minimum_funding_native": "0",
        "rpc_ok": False,
    }
    store = SolanaWalletStore(app.csv_dir, app.data_dir)
    try:
        cfg = _sol.settings(app)
        rpc_url = str(cfg.get("rpc_url") or _sol.DEFAULT_RPC).strip()
    except Exception:
        rpc_url = _sol.DEFAULT_RPC

    balances: list[Decimal] = []
    reserves: list[Decimal] = []
    trades: list[Decimal] = []
    last_error = ""
    for row in rows:
        tid = str(row.get("telegram_id") or "").strip()
        if not tid:
            continue
        try:
            meta = store.get_meta(tid)
            address = str(meta.get("address") or "").strip()
            if not address:
                continue
            rpc_result = _rpc(rpc_url, "getBalance", [address, {"commitment": "confirmed"}]) or {}
            lamports = int((rpc_result or {}).get("value") or 0)
            balance = Decimal(lamports) / Decimal(1_000_000_000)
            reserve = max(
                Decimal(0),
                _dec(user_setting(app.csv_dir, tid, _sol.SOLANA_CHAIN_ID, "solana_live_min_reserve_sol", "0.005"), "0.005"),
            )
            trade = max(
                Decimal(0),
                _dec(user_setting(app.csv_dir, tid, _sol.SOLANA_CHAIN_ID, "solana_live_trade_sol", "0.0005"), "0.0005"),
            )
            balances.append(balance)
            reserves.append(reserve)
            trades.append(trade)
        except Exception as exc:
            last_error = type(exc).__name__

    total = sum(balances, Decimal(0))
    reserve_total = sum(reserves, Decimal(0))
    trade_total = sum(trades, Decimal(0))
    usable = sum((max(Decimal(0), b - r) for b, r in zip(balances, reserves)), Decimal(0))
    result.update({
        "wallet_count": len(balances),
        "balance_native": str(total),
        "reserved_native": str(reserve_total),
        "usable_native": str(usable),
        "configured_trade_native": str(trade_total),
        "minimum_funding_native": str(reserve_total + trade_total),
        "rpc_ok": bool(balances),
    })
    if not balances and last_error:
        result["error"] = last_error
    return result


def _asset_snapshot(app) -> dict:
    now = time.time()
    with _CACHE_LOCK:
        if _CACHE["data"] and now - float(_CACHE["ts"] or 0) < CACHE_SECONDS:
            return dict(_CACHE["data"])

    rows = _control_rows(app)
    data = {
        "configured_accounts": len(rows),
        "base": _base_balance(app, rows),
        "solana": _solana_balance(app, rows),
        "addresses_redacted": True,
        "private_key_access": False,
        "updated_epoch": int(now),
        "cache_seconds": CACHE_SECONDS,
    }
    with _CACHE_LOCK:
        _CACHE["ts"] = now
        _CACHE["data"] = dict(data)
    return data


def snapshot(app) -> dict:
    out = _PREV_SNAPSHOT(app)
    out["schema_version"] = max(3, int(out.get("schema_version") or 0))
    try:
        out["native_assets"] = _asset_snapshot(app)
    except Exception as exc:
        out["native_assets"] = {
            "addresses_redacted": True,
            "private_key_access": False,
            "error": type(exc).__name__,
        }
    return out


def install() -> None:
    if getattr(_diag, "_sibot1_asset_diag_installed", False):
        return
    _diag.snapshot = snapshot
    _diag._sibot1_asset_diag_installed = True
    print("[sibot1-asset-diag] redacted-native-balances=true addresses=false private-key-access=false cache=60s")


install()
