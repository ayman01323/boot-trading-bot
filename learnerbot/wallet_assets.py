from __future__ import annotations

import csv
from pathlib import Path
from web3 import Web3

from .live_executor import LiveTrader

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def _rows(path: Path):
    if not path.exists(): return []
    with path.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))


def _known_tokens(app, chain_id: int) -> set[str]:
    out=set()
    for r in _rows(Path(app.csv_dir)/"tokens.csv"):
        if (r.get("chain_id") or "").strip()!=str(chain_id):continue
        a=(r.get("address") or "").strip()
        if a.startswith("0x") and len(a)==42:out.add(a.lower())
    for r in _rows(Path(app.csv_dir)/"auto"/"live_trade_audit.csv"):
        if (r.get("chain_id") or "").strip()!=str(chain_id):continue
        a=(r.get("token") or "").strip()
        if a.startswith("0x") and len(a)==42:out.add(a.lower())
    for r in _rows(Path(app.csv_dir)/"live_opportunities.csv"):
        if (r.get("chain_id") or "").strip()!=str(chain_id):continue
        for a in (r.get("route_path") or "").split(">"):
            a=a.strip()
            if a.startswith("0x") and len(a)==42:out.add(a.lower())
    return out


def _recent_transfer_tokens(trader: LiveTrader, blocks: int = 1500) -> set[str]:
    """Best-effort recent token discovery. Public RPCs may reject wide log ranges."""
    out=set(); latest=int(trader.w3.eth.block_number); start=max(0,latest-max(0,blocks))
    padded="0x"+("0"*24)+trader.address[2:].lower()
    for topics in ([TRANSFER_TOPIC,None,padded],[TRANSFER_TOPIC,padded]):
        try:
            logs=trader.w3.eth.get_logs({"fromBlock":start,"toBlock":latest,"topics":topics})
            for log in logs[:2000]: out.add(str(log["address"]).lower())
        except Exception:
            pass
    return out


def wallet_assets(app, chain_slug: str, discover_recent=True, *, telegram_id=None, wallet_id=None) -> dict:
    t=LiveTrader(app,chain_slug,telegram_id=telegram_id,wallet_id=wallet_id)
    addresses=_known_tokens(app,t.chain.chain_id)
    if discover_recent:addresses |= _recent_transfer_tokens(t)
    items=[]
    for a in list(addresses)[:100]:
        if a.lower()==t.wrapped.lower():
            # still include non-zero wrapped balance
            pass
        try:
            _,_,dec,sym,raw,bal=t.token_balance(a)
            if raw>0:items.append({"address":a,"symbol":sym,"decimals":dec,"balance":bal})
        except Exception:
            continue
    items.sort(key=lambda x:(x["symbol"].upper(),x["address"]))
    return {"chain":t.chain.slug,"name":t.chain.name,"wallet":t.address,"native_symbol":t.chain.native_symbol,"native_balance":t.native_balance(),"tokens":items}
