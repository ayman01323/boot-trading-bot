from __future__ import annotations

import hashlib
import time
from collections import defaultdict

def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))

def learn_patterns(conn, min_txs: int = 3) -> int:
    rows = conn.execute(
        """SELECT p.*,t.selector,w.bot_score
           FROM profit_evidence p
           JOIN transactions t ON t.tx_hash=p.tx_hash
           LEFT JOIN wallet_scores w ON w.wallet=p.wallet
           WHERE p.route_fingerprint IS NOT NULL AND p.route_fingerprint!=''"""
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        key = r["route_fingerprint"]
        groups[key].append(r)

    now = int(time.time())
    written = 0
    for fingerprint, items in groups.items():
        if len(items) < min_txs:
            continue
        wallets = {r["wallet"] for r in items}
        executor = items[0]["executor"]
        selector = items[0]["selector"]
        proven = [r for r in items if r["proof_quality"] == "PROVEN_WRAPPED_BASE"]
        positive = [r for r in proven if r["net_base"] is not None and r["net_base"] > 0]
        avg_net = sum(float(r["net_base"]) for r in positive) / len(positive) if positive else None
        classes = defaultdict(int)
        for r in items:
            classes[r["classification"]] += 1
        strategy_class = max(classes, key=classes.get)

        # Confidence: repeated evidence + multiple wallets + profit proofs.
        repeat_component = min(45.0, len(items) * 3.0)
        wallet_component = min(20.0, len(wallets) * 5.0)
        proof_component = min(30.0, len(proven) * 4.0)
        score_component = min(5.0, sum(float(r["bot_score"] or 0) for r in items) / len(items) / 20)
        confidence = _clamp(repeat_component + wallet_component + proof_component + score_component)

        # Replicability rewards repeated public route structure and evidence across wallets;
        # it is deliberately conservative when only one wallet is observed.
        replicability = 25.0
        replicability += min(30.0, len(items) * 2.0)
        replicability += min(20.0, max(0, len(wallets)-1) * 6.0)
        replicability += min(20.0, len(positive) * 3.0)
        if len(wallets) == 1:
            replicability -= 10.0
        replicability = _clamp(replicability)

        pattern_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
        conn.execute(
            """INSERT INTO strategy_patterns(
                pattern_id,executor,selector,route_fingerprint,strategy_class,tx_count,wallet_count,
                proven_profit_count,positive_count,avg_net_base,base_symbol,confidence,replicability,status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(pattern_id) DO UPDATE SET
                executor=excluded.executor,selector=excluded.selector,route_fingerprint=excluded.route_fingerprint,
                strategy_class=excluded.strategy_class,tx_count=excluded.tx_count,wallet_count=excluded.wallet_count,
                proven_profit_count=excluded.proven_profit_count,positive_count=excluded.positive_count,
                avg_net_base=excluded.avg_net_base,base_symbol=excluded.base_symbol,
                confidence=excluded.confidence,replicability=excluded.replicability,
                status=excluded.status,updated_at=excluded.updated_at""",
            (
                pattern_id,executor,selector,fingerprint,strategy_class,len(items),len(wallets),
                len(proven),len(positive),avg_net,(proven[0]["base_symbol"] if proven else None),
                round(confidence,2),round(replicability,2),"SHADOW_CANDIDATE",now
            ),
        )
        written += 1
    conn.commit()
    return written
