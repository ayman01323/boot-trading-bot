#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import sys
from decimal import Decimal
from pathlib import Path

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learnerbot.config import AppSettings, load_chains

ERC20_ABI = [
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"string"}]},
]
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ADDRESS_FIELDS = {"address","token","token_address","contract","contract_address","token0","token1","base_token","quote_token","wrapped_address"}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _fmt(v: Decimal) -> str:
    s = format(v, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _tokens(csv_dir: Path, chain_id: int, wrapped: str, limit: int = 120) -> list[str]:
    paths = [
        csv_dir / "tokens.csv",
        csv_dir / "token_seeds.csv",
        csv_dir / "trading_products.csv",
        csv_dir / "auto" / "product_universe.csv",
        csv_dir / "auto" / "pool_registry.csv",
        csv_dir / "auto" / "v3_pool_registry.csv",
    ]
    out: list[str] = []
    seen: set[str] = set()

    def add(value):
        a = str(value or "").strip()
        if not ADDRESS_RE.match(a):
            return
        try:
            a = Web3.to_checksum_address(a)
        except Exception:
            return
        k = a.lower()
        if k not in seen:
            seen.add(k)
            out.append(a)

    add(wrapped)
    for path in paths:
        for row in _rows(path):
            scope = str(row.get("chain_id") or row.get("chain") or "").strip()
            if scope and scope not in {str(chain_id), "*"}:
                continue
            for key, value in row.items():
                if str(key or "").strip().lower() in ADDRESS_FIELDS:
                    add(value)
                    if len(out) >= limit:
                        return out
    return out


def _connect(chain):
    last = None
    for url in chain.rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 12}))
            if w3.is_connected() and int(w3.eth.chain_id) == int(chain.chain_id):
                return w3
        except Exception as exc:
            last = exc
    raise RuntimeError(str(last or "no working RPC"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only EVM wallet balances using BOOT configured RPCs")
    ap.add_argument("--wallet", required=True)
    args = ap.parse_args()
    if not ADDRESS_RE.match(args.wallet):
        ap.error("wallet must be a 0x address with 40 hexadecimal characters")

    wallet = Web3.to_checksum_address(args.wallet)
    app = AppSettings.load()
    chains = load_chains(app, enabled_only=False)

    print(f"Wallet: {wallet}")
    print("=" * 100)
    for chain in chains:
        if not chain.rpc_urls:
            print(f"{chain.slug:<12} RPC MISSING")
            continue
        try:
            w3 = _connect(chain)
            native = Decimal(int(w3.eth.get_balance(wallet))) / Decimal(10**18)
            print(f"{chain.slug:<12} {chain.native_symbol:<12} {_fmt(native)}")
            for token in _tokens(Path(app.csv_dir), chain.chain_id, chain.wrapped_base_address):
                try:
                    c = w3.eth.contract(address=token, abi=ERC20_ABI)
                    raw = int(c.functions.balanceOf(wallet).call())
                    if raw <= 0:
                        continue
                    try:
                        decimals = max(0, min(36, int(c.functions.decimals().call())))
                    except Exception:
                        decimals = 18
                    try:
                        symbol = str(c.functions.symbol().call()).strip()[:24] or token[:10]
                    except Exception:
                        symbol = token[:10]
                    bal = Decimal(raw) / (Decimal(10) ** decimals)
                    print(f"{'':<12} {symbol:<12} {_fmt(bal):<28} {token}")
                except Exception:
                    continue
        except Exception as exc:
            print(f"{chain.slug:<12} ERROR {type(exc).__name__}: {str(exc)[:140]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
