from __future__ import annotations
from typing import Optional

import math
import time
from collections import Counter
from .config import load_addresses, load_scoring_weights

def _ratio_top(values: list[Optional[str]]) -> float:
    vals = [x for x in values if x]
    if not vals:
        return 0.0
    return Counter(vals).most_common(1)[0][1] / len(vals)

def _score_count(n: int) -> float:
    if n <= 1:
        return 0.0
    # 2->small, 10->moderate, 100+->near max
    return min(1.0, math.log10(n + 1) / 2.2)

def _score_rate(per_min: float) -> float:
    if per_min >= 20: return 1.0
    if per_min >= 5: return 0.85
    if per_min >= 1: return 0.65
    if per_min >= 0.2: return 0.35
    return 0.10 if per_min > 0 else 0.0

def compute_scores(conn, csv_dir, chain_id=None) -> int:
    builders = load_addresses(csv_dir, "builders.csv", chain_id)
    blocklist = set(load_addresses(csv_dir, "wallet_blocklist.csv", chain_id))
    weights = load_scoring_weights(csv_dir, chain_id)
    wallets = [r["from_addr"] for r in conn.execute("SELECT DISTINCT from_addr FROM transactions")]

    now = int(time.time())
    written = 0
    for wallet in wallets:
        if wallet in blocklist:
            continue
        rows = conn.execute(
            """SELECT t.*, b.timestamp FROM transactions t
               JOIN blocks b ON b.number=t.block_number
               WHERE t.from_addr=? ORDER BY b.timestamp,t.tx_index""",
            (wallet,),
        ).fetchall()
        if not rows:
            continue
        tx_count = len(rows)
        first_ts = rows[0]["timestamp"]
        last_ts = rows[-1]["timestamp"]
        span_min = max((last_ts - first_ts) / 60.0, 1/60)
        tx_per_min = tx_count / span_min
        tos = [r["to_addr"] for r in rows]
        sels = [r["selector"] for r in rows if r["selector"] and r["selector"] != "0x"]
        repeat_to = _ratio_top(tos)
        repeat_sel = _ratio_top(sels)
        zero_ratio = sum(1 for r in rows if int(r["value_wei"]) == 0) / tx_count
        builder_count = sum(1 for r in rows if (r["to_addr"] or "") in builders)
        builder_feature = min(1.0, builder_count / max(1, tx_count * 0.10))

        raw = (
            weights["tx_count"] * _score_count(tx_count)
            + weights["tx_rate"] * _score_rate(tx_per_min)
            + weights["repeat_to"] * repeat_to
            + weights["repeat_selector"] * repeat_sel
            + weights["zero_value"] * zero_ratio
            + weights["builder"] * builder_feature
        )
        max_weight = sum(weights.values())
        bot_score = round(100.0 * raw / max_weight, 2) if max_weight else 0.0

        primary_executor = Counter([x for x in tos if x]).most_common(1)
        primary_executor = primary_executor[0][0] if primary_executor else None

        conn.execute(
            """INSERT INTO wallet_scores(
                wallet,tx_count,first_ts,last_ts,tx_per_min,repeat_to_ratio,repeat_selector_ratio,
                zero_value_ratio,builder_tx_count,bot_score,primary_executor,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(wallet) DO UPDATE SET
                tx_count=excluded.tx_count,first_ts=excluded.first_ts,last_ts=excluded.last_ts,
                tx_per_min=excluded.tx_per_min,repeat_to_ratio=excluded.repeat_to_ratio,
                repeat_selector_ratio=excluded.repeat_selector_ratio,zero_value_ratio=excluded.zero_value_ratio,
                builder_tx_count=excluded.builder_tx_count,bot_score=excluded.bot_score,
                primary_executor=excluded.primary_executor,updated_at=excluded.updated_at""",
            (
                wallet, tx_count, first_ts, last_ts, tx_per_min, repeat_to, repeat_sel,
                zero_ratio, builder_count, bot_score, primary_executor, now
            ),
        )
        written += 1
    conn.commit()
    return written
