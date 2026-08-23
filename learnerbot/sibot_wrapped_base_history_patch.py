from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from decimal import Decimal

from web3 import Web3

from . import sibot as _sibot

# Canonical wrapped-native assets for the EVM chains currently supported by BOOT.
# These are protocol addresses, not operator configuration or secrets.
_WRAPPED_BASE = {
    1: "0xc02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower(),       # WETH
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c".lower(),     # WBNB
    137: "0x0d500B1d8E8eD2eE21C99d1DB9A6444d3ADf1270".lower(),    # WPOL/WMATIC
    42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1".lower(),  # WETH
    8453: "0x4200000000000000000000000000000000000006".lower(),   # WETH
}

_PREV_RECONSTRUCT = _sibot.reconstruct_spot_trades


def _successful_normal(row: dict) -> bool:
    if str(row.get("isError") or "0") == "1":
        return False
    return str(row.get("txreceipt_status") or "1") not in {"0", "false", "False"}


def reconstruct_spot_trades(
    wallet: str,
    routers: set[str],
    normal_rows: list[dict],
    token_rows: list[dict],
    internal_rows: list[dict],
    chain_id: int,
    chain_slug: str,
) -> tuple[list[dict], int]:
    """Reconstruct direct native *and* canonical wrapped-native spot trades.

    The original reconstructor only recognised native-coin buys/sells. Modern EVM
    wallets frequently trade WETH/WBNB/WPOL directly, so a wallet could have
    thousands of proven token-transfer rows but still produce zero SiBot history.

    This keeps every existing fail-closed rule: the wallet must originate the tx,
    the tx must target a configured router, exactly one non-base token must move in
    the directional leg, failed transactions are ignored, and FIFO matching is used.
    No leader-quality, LIVE, capital, signing or execution threshold is changed.
    """
    wrapped = _WRAPPED_BASE.get(int(chain_id), "")
    if not wrapped:
        return _PREV_RECONSTRUCT(
            wallet, routers, normal_rows, token_rows, internal_rows, chain_id, chain_slug
        )

    w = str(wallet or "").lower()
    normals = {
        str(row.get("hash") or "").lower(): row
        for row in normal_rows
        if _successful_normal(row) and str(row.get("from") or "").lower() == w
    }

    flows = defaultdict(
        lambda: defaultdict(lambda: {"in": 0, "out": 0, "symbol": "", "decimals": 18})
    )
    for row in token_rows:
        tx_hash = str(row.get("hash") or "").lower()
        if tx_hash not in normals:
            continue
        token = str(row.get("contractAddress") or "").lower()
        if not Web3.is_address(token):
            continue
        raw = _sibot._int(row.get("value"), 0)
        if raw <= 0:
            continue
        frm = str(row.get("from") or "").lower()
        to = str(row.get("to") or "").lower()
        item = flows[tx_hash][token]
        item["symbol"] = str(row.get("tokenSymbol") or token[:10])[:32]
        item["decimals"] = max(0, min(36, _sibot._int(row.get("tokenDecimal"), 18)))
        if to == w and frm != w:
            item["in"] += raw
        if frm == w and to != w:
            item["out"] += raw

    internal_in = defaultdict(Decimal)
    for row in internal_rows:
        if str(row.get("isError") or "0") == "1":
            continue
        if str(row.get("to") or "").lower() != w:
            continue
        tx_hash = str(row.get("hash") or "").lower()
        if tx_hash in normals:
            internal_in[tx_hash] += (
                Decimal(str(_sibot._int(row.get("value"), 0))) / Decimal(10**18)
            )

    events: list[dict] = []
    for tx_hash, tx in normals.items():
        to = str(tx.get("to") or "").lower()
        if routers and to not in routers:
            continue
        ts = _sibot._int(tx.get("timeStamp"), 0)
        native_value = Decimal(str(_sibot._int(tx.get("value"), 0))) / Decimal(10**18)
        gas = (
            Decimal(str(_sibot._int(tx.get("gasUsed"), 0)))
            * Decimal(str(_sibot._int(tx.get("gasPrice"), 0)))
            / Decimal(10**18)
        )

        token_items = []
        for token, flow in flows.get(tx_hash, {}).items():
            if token == wrapped:
                continue
            net = int(flow["in"]) - int(flow["out"])
            if net:
                token_items.append((token, net, flow))
        positive = [item for item in token_items if item[1] > 0]
        negative = [item for item in token_items if item[1] < 0]

        wrapped_flow = flows.get(tx_hash, {}).get(wrapped) or {}
        wrapped_net = int(wrapped_flow.get("in", 0)) - int(wrapped_flow.get("out", 0))

        # Existing direct-native BUY path.
        if native_value > 0 and len(positive) == 1 and not negative:
            token, raw, meta = positive[0]
            refund = internal_in.get(tx_hash, Decimal(0))
            principal = max(Decimal(0), native_value - refund)
            if principal > 0:
                events.append(
                    {
                        "kind": "BUY",
                        "base_mode": "native",
                        "tx": tx_hash,
                        "ts": ts,
                        "token": token,
                        "raw": int(raw),
                        "symbol": meta["symbol"],
                        "decimals": meta["decimals"],
                        "principal": principal,
                        "gas": gas,
                    }
                )
            continue

        # Wrapped-native BUY: wallet sends WETH/WBNB/WPOL and receives exactly one
        # non-base token. Requiring tx.value==0 prevents double-counting native buys.
        if (
            native_value == 0
            and wrapped_net < 0
            and len(positive) == 1
            and not negative
        ):
            token, raw, meta = positive[0]
            principal = Decimal(abs(wrapped_net)) / Decimal(10**18)
            if principal > 0:
                events.append(
                    {
                        "kind": "BUY",
                        "base_mode": "wrapped",
                        "tx": tx_hash,
                        "ts": ts,
                        "token": token,
                        "raw": int(raw),
                        "symbol": meta["symbol"],
                        "decimals": meta["decimals"],
                        "principal": principal,
                        "gas": gas,
                    }
                )
            continue

        # Existing direct-native SELL path.
        if (
            native_value == 0
            and len(negative) == 1
            and not positive
            and internal_in.get(tx_hash, Decimal(0)) > 0
        ):
            token, raw, meta = negative[0]
            events.append(
                {
                    "kind": "SELL",
                    "base_mode": "native",
                    "tx": tx_hash,
                    "ts": ts,
                    "token": token,
                    "raw": abs(int(raw)),
                    "symbol": meta["symbol"],
                    "decimals": meta["decimals"],
                    "principal": internal_in[tx_hash],
                    "gas": gas,
                }
            )
            continue

        # Wrapped-native SELL: wallet sends exactly one non-base token and receives
        # WETH/WBNB/WPOL. If native proceeds are also present, the direct-native
        # path above remains authoritative and this branch is not used.
        if (
            native_value == 0
            and wrapped_net > 0
            and len(negative) == 1
            and not positive
            and internal_in.get(tx_hash, Decimal(0)) <= 0
        ):
            token, raw, meta = negative[0]
            events.append(
                {
                    "kind": "SELL",
                    "base_mode": "wrapped",
                    "tx": tx_hash,
                    "ts": ts,
                    "token": token,
                    "raw": abs(int(raw)),
                    "symbol": meta["symbol"],
                    "decimals": meta["decimals"],
                    "principal": Decimal(wrapped_net) / Decimal(10**18),
                    "gas": gas,
                }
            )

    events.sort(key=lambda item: (item["ts"], item["tx"]))
    lots = defaultdict(deque)
    trades: list[dict] = []
    unmatched_sells = 0
    match_i = 0

    for event in events:
        token = event["token"]
        if event["kind"] == "BUY":
            lots[token].append(
                {
                    **event,
                    "remaining": int(event["raw"]),
                    "remaining_cost": event["principal"] + event["gas"],
                }
            )
            continue

        remaining = int(event["raw"])
        original = max(1, remaining)
        while remaining > 0 and lots[token]:
            lot = lots[token][0]
            qty = min(remaining, int(lot["remaining"]))
            buy_fraction = Decimal(qty) / Decimal(max(1, int(lot["remaining"])))
            cost = lot["remaining_cost"] * buy_fraction
            sell_fraction = Decimal(qty) / Decimal(original)
            proceeds = event["principal"] * sell_fraction
            sell_gas = event["gas"] * sell_fraction
            net = proceeds - sell_gas - cost
            match_i += 1
            trade_id = hashlib.sha256(
                f"{chain_id}|{w}|{lot['tx']}|{event['tx']}|{token}|{match_i}".encode()
            ).hexdigest()[:32]
            wrapped_used = lot.get("base_mode") == "wrapped" or event.get("base_mode") == "wrapped"
            trades.append(
                {
                    "trade_id": trade_id,
                    "chain_id": chain_id,
                    "chain_slug": chain_slug,
                    "wallet": w,
                    "token": token,
                    "symbol": event["symbol"] or lot["symbol"],
                    "decimals": event["decimals"],
                    "buy_tx": lot["tx"],
                    "sell_tx": event["tx"],
                    "buy_ts": lot["ts"],
                    "sell_ts": event["ts"],
                    "token_amount_raw": str(qty),
                    "cost_native": str(cost),
                    "proceeds_native": str(proceeds),
                    "buy_gas_native": str(lot["gas"] * buy_fraction),
                    "sell_gas_native": str(sell_gas),
                    "net_native": str(net),
                    "source": "WRAPPED_BASE_DIRECT_FIFO" if wrapped_used else "ETHERSCAN_DIRECT_NATIVE_FIFO",
                    "updated_at": int(time.time()),
                }
            )
            lot["remaining"] -= qty
            lot["remaining_cost"] -= cost
            remaining -= qty
            if lot["remaining"] <= 0:
                lots[token].popleft()
        if remaining > 0:
            unmatched_sells += 1

    return trades, unmatched_sells


def install() -> None:
    if getattr(_sibot, "_wrapped_base_history_patch_installed", False):
        return
    _sibot.reconstruct_spot_trades = reconstruct_spot_trades
    _sibot._wrapped_base_history_patch_installed = True
    print(
        "[sibot-wrapped-base-history] native_plus_wrapped=true "
        "chains=1,56,137,42161,8453 thresholds=unchanged"
    )


install()
