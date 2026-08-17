#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learnerbot.config import AppSettings, load_chains

ERC20_META_ABI = [
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"string"}]},
]

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ADDRESS_FIELDS = {
    "address", "token", "token_address", "contract", "contract_address",
    "token0", "token1", "base_token", "quote_token", "wrapped_address",
}


def rows(path: Path) -> list[dict]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def token_candidates(csv_dir: Path, chain_id: int, wrapped: str, max_tokens: int) -> list[str]:
    """Collect token addresses from local CSV state without external token-list APIs."""
    files = [
        csv_dir / "token_seeds.csv",
        csv_dir / "tokens.csv",
        csv_dir / "trading_products.csv",
        csv_dir / "auto" / "product_universe.csv",
        csv_dir / "auto" / "pool_registry.csv",
        csv_dir / "auto" / "v3_pool_registry.csv",
    ]
    out: list[str] = []

    def add(v):
        s = str(v or "").strip()
        if not ADDRESS_RE.match(s):
            return
        try:
            a = Web3.to_checksum_address(s)
        except Exception:
            return
        if a.lower() not in {x.lower() for x in out}:
            out.append(a)

    add(wrapped)
    for path in files:
        for r in rows(path):
            row_chain = str(r.get("chain_id") or r.get("chain") or "").strip()
            if row_chain and row_chain not in {str(chain_id), "*"}:
                continue
            for k, v in r.items():
                if str(k or "").strip().lower() in ADDRESS_FIELDS:
                    add(v)
                    if len(out) >= max_tokens:
                        return out
    return out


def safe_symbol(c, fallback: str) -> str:
    try:
        s = c.functions.symbol().call()
        if isinstance(s, bytes):
            s = s.rstrip(b"\x00").decode("utf-8", "replace")
        s = str(s).strip()
        return s[:32] or fallback
    except Exception:
        return fallback


def safe_decimals(c) -> int:
    try:
        d = int(c.functions.decimals().call())
        return d if 0 <= d <= 36 else 18
    except Exception:
        return 18


def fmt(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only balance report across configured EVM chains")
    ap.add_argument("--wallet", required=True, help="0x EVM wallet address")
    ap.add_argument("--max-tokens-per-chain", type=int, default=120)
    ap.add_argument("--json-out", default="", help="Optional JSON output path")
    args = ap.parse_args()

    if not ADDRESS_RE.match(args.wallet):
        ap.error("wallet must be a 0x address with 40 hexadecimal characters")
    wallet = Web3.to_checksum_address(args.wallet)
    max_tokens = max(1, min(500, int(args.max_tokens_per_chain)))

    app = AppSettings.load()
    chains = load_chains(app, enabled_only=False)
    report = {"wallet": wallet, "chains": []}

    print(f"Wallet: {wallet}")
    print("=" * 96)

    for ch in chains:
        entry = {
            "chain_id": ch.chain_id,
            "chain_slug": ch.slug,
            "chain_name": ch.name,
            "enabled": bool(ch.enabled),
            "native_symbol": ch.native_symbol,
            "native_balance": None,
            "tokens": [],
            "rpc_ok": False,
            "error": "",
        }
        if not ch.rpc_urls:
            entry["error"] = "no RPC configured"
            report["chains"].append(entry)
            print(f"{ch.slug:<12} ERROR  no RPC configured")
            continue

        try:
            w3 = Web3(Web3.HTTPProvider(ch.rpc_urls[0], request_kwargs={"timeout": 15}))
            if not w3.is_connected():
                raise RuntimeError("RPC not connected")
            rpc_chain = int(w3.eth.chain_id)
            if rpc_chain != int(ch.chain_id):
                raise RuntimeError(f"RPC chain mismatch: got {rpc_chain}")
            entry["rpc_ok"] = True

            native = Decimal(int(w3.eth.get_balance(wallet))) / Decimal(10**18)
            entry["native_balance"] = fmt(native)
            print(f"{ch.slug:<12} {ch.native_symbol:<10} {fmt(native)}")

            wrapped = Web3.to_checksum_address(ch.wrapped_base_address)
            for token in token_candidates(Path(app.csv_dir), ch.chain_id, wrapped, max_tokens):
                try:
                    if not w3.eth.get_code(token):
                        continue
                    c = w3.eth.contract(address=token, abi=ERC20_META_ABI)
                    raw = int(c.functions.balanceOf(wallet).call())
                    if raw <= 0:
                        continue
                    dec = safe_decimals(c)
                    sym = safe_symbol(c, token[:10])
                    bal = Decimal(raw) / (Decimal(10) ** dec)
                    item = {"symbol": sym, "address": token, "decimals": dec, "balance": fmt(bal), "raw": str(raw)}
                    entry["tokens"].append(item)
                    print(f"{'':<12} {sym:<10} {fmt(bal):<26} {token}")
                except Exception:
                    continue
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{ch.slug:<12} ERROR  {entry['error']}")

        report["chains"].append(entry)

    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nJSON: {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
