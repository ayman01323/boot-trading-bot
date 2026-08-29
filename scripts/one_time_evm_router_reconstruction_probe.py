from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from decimal import Decimal

from learnerbot.config import AppSettings, load_chains
from learnerbot.multichain import contexts, close_contexts
from learnerbot import sibot
from learnerbot import sibot_alchemy_history_patch as ah
from learnerbot import sibot_wrapped_base_history_patch as wb

TARGET_CHAINS = (56, 42161)


def main() -> int:
    app = AppSettings.load()
    ctxs = contexts(app, enabled_only=True, with_rpc=False)
    try:
        evidence = []
        for ctx in ctxs:
            cid = int(ctx.config.chain_id)
            if cid not in TARGET_CHAINS:
                continue
            rows = ctx.conn.execute(
                """SELECT wallet,
                          COUNT(*) AS rows_n,
                          SUM(CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN 1 ELSE 0 END) AS proven_n,
                          SUM(CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN COALESCE(net_base,0) ELSE 0 END) AS proven_net
                   FROM profit_evidence
                   GROUP BY wallet
                   HAVING SUM(CASE WHEN proof_quality='PROVEN_WRAPPED_BASE' THEN COALESCE(net_base,0) ELSE 0 END) > 0
                   ORDER BY proven_net DESC, proven_n DESC
                   LIMIT 5"""
            ).fetchall()
            for row in rows:
                evidence.append({
                    "chain_id": cid,
                    "chain_slug": ctx.config.slug,
                    "wallet": str(row["wallet"]).lower(),
                    "profit_evidence_rows": int(row["rows_n"] or 0),
                    "proven_rows": int(row["proven_n"] or 0),
                    "proven_net_base": float(row["proven_net"] or 0),
                })
        if not evidence:
            print("EVM_ROUTER_RECONSTRUCTION_PROBE=" + json.dumps({"status": "NO_HISTORICALLY_PROFITABLE_BSC_OR_ARBITRUM_WALLET_FOUND"}))
            return 2

        evidence.sort(key=lambda r: (r["proven_net_base"], r["proven_rows"]), reverse=True)
        chosen = evidence[0]
        chain = next(c for c in load_chains(app, enabled_only=True) if int(c.chain_id) == chosen["chain_id"])
        wallet = chosen["wallet"]
        url = ah.alchemy_rpc_url(app, int(chain.chain_id))
        if not url:
            print("EVM_ROUTER_RECONSTRUCTION_PROBE=" + json.dumps({"status": "ALCHEMY_ENDPOINT_MISSING", "chosen_wallet": chosen}))
            return 3

        cfg = sibot.platform_settings(app, int(chain.chain_id))
        fetch_days = max(30, min(3650, sibot._int(cfg.get("history_fetch_days"), 365)))
        cutoff = int(time.time()) - fetch_days * 86400
        max_pages = max(1, min(40, sibot._int(cfg.get("history_max_pages"), 3)))
        page_size = max(100, min(1000, sibot._int(cfg.get("history_page_size"), 1000)))
        delay = max(0.0, min(2.0, sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))

        outbound, c_out = ah._asset_pages(url, wallet, "fromAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay)
        inbound, c_in = ah._asset_pages(url, wallet, "toAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay)
        transfers = ah._dedupe(outbound + inbound)
        normal, outgoing_hashes, ts_by_hash = ah._tx_context(url, transfers, wallet)
        token, _ = ah._normalised_transfer_rows(transfers)
        try:
            internal_transfers, c_internal = ah._asset_pages(url, wallet, "toAddress", ["internal"], cutoff, max_pages, page_size, delay)
            _, internal = ah._normalised_transfer_rows(ah._dedupe(internal_transfers))
        except Exception:
            internal = ah._trace_internal(url, wallet, outgoing_hashes, ts_by_hash)
            c_internal = True

        routers = {str(x).lower() for x in sibot._routers(app, chain)}
        wrapped = wb._WRAPPED_BASE.get(int(chain.chain_id), "")
        w = wallet.lower()
        normals = {
            str(row.get("hash") or "").lower(): row
            for row in normal
            if str(row.get("from") or "").lower() == w
        }

        flows = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0, "symbol": "", "decimals": 18}))
        for row in token:
            txh = str(row.get("hash") or "").lower()
            if txh not in normals:
                continue
            tok = str(row.get("contractAddress") or "").lower()
            raw = sibot._int(row.get("value"), 0)
            if not tok or raw <= 0:
                continue
            frm = str(row.get("from") or "").lower()
            to = str(row.get("to") or "").lower()
            item = flows[txh][tok]
            item["symbol"] = str(row.get("tokenSymbol") or tok[:10])
            item["decimals"] = sibot._int(row.get("tokenDecimal"), 18)
            if to == w and frm != w:
                item["in"] += raw
            if frm == w and to != w:
                item["out"] += raw

        internal_in = defaultdict(Decimal)
        for row in internal:
            if str(row.get("isError") or "0") == "1" or str(row.get("to") or "").lower() != w:
                continue
            txh = str(row.get("hash") or "").lower()
            if txh in normals:
                internal_in[txh] += Decimal(str(sibot._int(row.get("value"), 0))) / Decimal(10**18)

        reasons = Counter()
        top_unknown_destinations = Counter()
        router_txs = buys = sells = shadow_router_like = 0
        shadow_examples = []
        for txh, tx in normals.items():
            if str(tx.get("isError") or "0") == "1" or str(tx.get("txreceipt_status") or "1") in {"0", "false", "False"}:
                reasons["FAILED_TRANSACTION"] += 1
                continue
            destination = str(tx.get("to") or "").lower()
            is_router = (not routers) or destination in routers
            if is_router:
                router_txs += 1
            else:
                reasons["TOP_LEVEL_DESTINATION_NOT_RECOGNISED_ROUTER"] += 1
                if destination:
                    top_unknown_destinations[destination] += 1

            native_value = Decimal(str(sibot._int(tx.get("value"), 0))) / Decimal(10**18)
            token_items = []
            for tok, flow in flows.get(txh, {}).items():
                if tok == wrapped:
                    continue
                net = int(flow["in"]) - int(flow["out"])
                if net:
                    token_items.append((tok, net, flow))
            positive = [item for item in token_items if item[1] > 0]
            negative = [item for item in token_items if item[1] < 0]
            wf = flows.get(txh, {}).get(wrapped) or {}
            wrapped_net = int(wf.get("in", 0)) - int(wf.get("out", 0))
            buy_like = (
                (native_value > 0 and len(positive) == 1 and not negative)
                or (native_value == 0 and wrapped_net < 0 and len(positive) == 1 and not negative)
            )
            sell_like = (
                (native_value == 0 and len(negative) == 1 and not positive and internal_in.get(txh, Decimal(0)) > 0)
                or (native_value == 0 and wrapped_net > 0 and len(negative) == 1 and not positive and internal_in.get(txh, Decimal(0)) <= 0)
            )

            if is_router:
                if buy_like:
                    buys += 1
                elif sell_like:
                    sells += 1
                elif len(positive) > 1 or len(negative) > 1:
                    reasons["RECOGNISED_ROUTER_AMBIGUOUS_MULTI_TOKEN_FLOW"] += 1
                elif not positive and not negative:
                    reasons["RECOGNISED_ROUTER_NO_NON_BASE_NET_TOKEN_FLOW"] += 1
                else:
                    reasons["RECOGNISED_ROUTER_UNSUPPORTED_BASE_FLOW"] += 1
            elif buy_like or sell_like:
                shadow_router_like += 1
                if len(shadow_examples) < 8:
                    shadow_examples.append({
                        "tx": txh,
                        "destination": destination,
                        "direction": "BUY" if buy_like else "SELL",
                        "non_base_tokens": len(token_items),
                    })

        trades, unmatched = sibot.reconstruct_spot_trades(
            wallet, routers, normal, token, internal, int(chain.chain_id), chain.slug
        )

        evidence_ctx = next(ctx for ctx in ctxs if int(ctx.config.chain_id) == int(chain.chain_id))
        profitable_destinations = evidence_ctx.conn.execute(
            """SELECT LOWER(COALESCE(t.to_addr,'')) AS destination,
                      COUNT(*) AS n,
                      SUM(CASE WHEN p.proof_quality='PROVEN_WRAPPED_BASE' THEN COALESCE(p.net_base,0) ELSE 0 END) AS net
               FROM profit_evidence p
               JOIN transactions t ON t.tx_hash=p.tx_hash
               WHERE LOWER(p.wallet)=? AND p.proof_quality='PROVEN_WRAPPED_BASE'
               GROUP BY LOWER(COALESCE(t.to_addr,''))
               ORDER BY net DESC, n DESC
               LIMIT 12""",
            (wallet,),
        ).fetchall()

        result = {
            "status": "OK",
            "chosen_wallet": chosen,
            "coverage_complete": bool(c_out and c_in and c_internal),
            "raw_alchemy_transfer_rows": len(transfers),
            "outgoing_transactions_in_reconstruction_universe": len(normals),
            "configured_router_count": len(routers),
            "recognised_router_transactions": router_txs,
            "buy_events": buys,
            "sell_events": sells,
            "matched_closed_trades": len(trades),
            "unmatched_sells": int(unmatched),
            "shadow_router_like_unrecognised_destination_txs": shadow_router_like,
            "rejection_reasons": dict(reasons.most_common()),
            "top_unrecognised_destinations": top_unknown_destinations.most_common(12),
            "shadow_router_like_examples": shadow_examples,
            "historically_profitable_destinations": [
                {
                    "destination": str(r["destination"] or ""),
                    "rows": int(r["n"] or 0),
                    "proven_net_base": float(r["net"] or 0),
                    "recognised_router": str(r["destination"] or "").lower() in routers,
                }
                for r in profitable_destinations
            ],
        }
        print("EVM_ROUTER_RECONSTRUCTION_PROBE=" + json.dumps(result, sort_keys=True))
        return 0
    finally:
        close_contexts(ctxs)


if __name__ == "__main__":
    raise SystemExit(main())
