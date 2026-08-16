from __future__ import annotations

import hashlib
import time
from .config import load_addresses

def _builder_payment(conn, wallet, block_number, tx_index, builders):
    if not builders:
        return 0.0
    rows = conn.execute(
        """SELECT to_addr,value_wei FROM transactions
           WHERE from_addr=? AND block_number=? AND tx_index>?
           ORDER BY tx_index LIMIT 3""",
        (wallet, block_number, tx_index),
    ).fetchall()
    return sum(
        int(r["value_wei"]) / 1e18
        for r in rows
        if (r["to_addr"] or "") in builders
    )

def _ordered_unique(seq):
    out = []
    seen = set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def analyse_tx(conn, settings, wallet, tx_hash, executor):
    tx = conn.execute("SELECT * FROM transactions WHERE tx_hash=?", (tx_hash,)).fetchone()
    if not tx:
        raise ValueError("Unknown transaction")

    cluster = {wallet.lower()}
    if executor:
        cluster.add(executor.lower())

    transfers = conn.execute(
        """SELECT tr.*,m.symbol,m.decimals
           FROM token_transfers tr
           LEFT JOIN token_meta m ON m.token=tr.token
           WHERE tr.tx_hash=?
           ORDER BY tr.log_index""",
        (tx_hash,),
    ).fetchall()

    deltas = {}
    token_info = {}
    token_order = []
    wrapped_in = False
    wrapped_out = False

    for tr in transfers:
        token = tr["token"]
        token_order.append(token)
        symbol = tr["symbol"] or token[:10]
        decimals = int(tr["decimals"] if tr["decimals"] is not None else 18)
        token_info[token] = (symbol, decimals)
        amount = int(tr["raw_amount"])
        frm = tr["from_addr"]
        to = tr["to_addr"]

        if symbol.upper() == settings.wrapped_base_symbol.upper():
            if frm in cluster and to not in cluster:
                wrapped_out = True
            elif to in cluster and frm not in cluster:
                wrapped_in = True

        if frm in cluster and to not in cluster:
            deltas[token] = deltas.get(token, 0) - amount
        elif to in cluster and frm not in cluster:
            deltas[token] = deltas.get(token, 0) + amount

    gas_price = int(tx["effective_gas_price_wei"] or tx["gas_price_wei"] or 0)
    gas_native = (int(tx["gas_used"] or 0) * gas_price) / 1e18
    builders = load_addresses(settings.csv_dir, "builders.csv", settings.chain_id)
    builder_native = _builder_payment(
        conn, wallet, int(tx["block_number"]), int(tx["tx_index"] or 0), builders
    )

    positive_tokens = []
    wrapped_delta = None
    wrapped_token = None
    wrapped_symbol = settings.wrapped_base_symbol

    for token, raw in deltas.items():
        symbol, decimals = token_info[token]
        unit = raw / (10 ** decimals)
        if unit > 0:
            positive_tokens.append((token, symbol, decimals, unit))
        if symbol.upper() == settings.wrapped_base_symbol.upper():
            wrapped_delta = unit
            wrapped_token = token
            wrapped_symbol = symbol

    proof = "NO_POSITIVE_DELTA"
    base_token = None
    base_symbol = None
    gross = 0.0
    net = None
    net_usd = None

    # Strongest profit/loss evidence: wrapped base crossed the wallet/executor
    # boundary in BOTH directions in the same transaction. This is a much
    # better closed-cycle signal than counting a simple wrapped-base inflow.
    if wrapped_delta is not None and wrapped_in and wrapped_out:
        base_token = wrapped_token
        base_symbol = wrapped_symbol
        gross = float(wrapped_delta)
        net = gross - gas_native - builder_native
        proof = "PROVEN_WRAPPED_BASE"
        if settings.native_usd is not None:
            net_usd = net * settings.native_usd
    elif wrapped_delta is not None and wrapped_delta > 0:
        base_token = wrapped_token
        base_symbol = wrapped_symbol
        gross = float(wrapped_delta)
        net = None
        proof = "WRAPPED_BASE_INFLOW_ONLY"
    elif wrapped_delta is not None and wrapped_delta < 0:
        base_token = wrapped_token
        base_symbol = wrapped_symbol
        gross = float(wrapped_delta)
        net = None
        proof = "WRAPPED_BASE_OUTFLOW_ONLY"
    elif positive_tokens:
        base_token, base_symbol, _, gross = max(positive_tokens, key=lambda x: x[3])
        net = gross
        proof = "TOKEN_DELTA_ONLY"
    elif int(tx["value_wei"] or 0) > 0:
        proof = "INCOMPLETE_NATIVE_TRACE"

    route_tokens = _ordered_unique(token_order)
    route_fingerprint = (
        f"{(executor or tx['to_addr'] or '')}|{tx['selector']}|"
        + ">".join(route_tokens[:12])
    )
    pattern_id = hashlib.sha256(route_fingerprint.encode()).hexdigest()[:20]
    unique_tokens = len(set(route_tokens))

    if proof == "PROVEN_WRAPPED_BASE" and unique_tokens >= 3:
        classification = "TRIANGULAR_OR_MULTI_HOP_ARBITRAGE_CANDIDATE"
    elif proof == "PROVEN_WRAPPED_BASE" and unique_tokens >= 2:
        classification = "TWO_ASSET_ARBITRAGE_CANDIDATE"
    elif proof == "WRAPPED_BASE_OUTFLOW_ONLY" and positive_tokens:
        classification = "TOKEN_BUY_OR_ENTRY_CANDIDATE"
    elif proof == "WRAPPED_BASE_INFLOW_ONLY":
        classification = "TOKEN_SALE_OR_EXIT_CANDIDATE"
    elif unique_tokens >= 2 and tx["selector"] != "0x":
        classification = "AUTOMATED_EXECUTOR_PATTERN"
    elif transfers:
        classification = "TRANSFER_OR_TREASURY_PATTERN"
    else:
        classification = "NO_TOKEN_FLOW"

    now = int(time.time())
    conn.execute(
        """INSERT INTO profit_evidence(
             tx_hash,wallet,executor,base_token,base_symbol,gross_delta,
             gas_bnb,builder_payment_bnb,net_base,net_usd,proof_quality,
             classification,route_fingerprint,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(tx_hash,wallet) DO UPDATE SET
             executor=excluded.executor,
             base_token=excluded.base_token,
             base_symbol=excluded.base_symbol,
             gross_delta=excluded.gross_delta,
             gas_bnb=excluded.gas_bnb,
             builder_payment_bnb=excluded.builder_payment_bnb,
             net_base=excluded.net_base,
             net_usd=excluded.net_usd,
             proof_quality=excluded.proof_quality,
             classification=excluded.classification,
             route_fingerprint=excluded.route_fingerprint,
             created_at=excluded.created_at""",
        (
            tx_hash, wallet, executor, base_token, base_symbol, gross,
            gas_native, builder_native, net, net_usd, proof,
            classification, route_fingerprint, now,
        ),
    )
    conn.commit()
    return {
        "tx_hash": tx_hash,
        "pattern_id": pattern_id,
        "proof_quality": proof,
        "classification": classification,
        "base_symbol": base_symbol,
        "gross_delta": gross,
        "net_base": net,
        "net_usd": net_usd,
        "route_fingerprint": route_fingerprint,
    }
