from __future__ import annotations

from .tokenmeta import get_token_meta

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

def _int(v, default=0):
    if v in (None, ""):
        return default
    if isinstance(v, int):
        return v
    return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v)

def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()

def ingest_receipt(conn, rpc, tx_hash: str, settings=None) -> int:
    receipt = rpc.receipt(tx_hash)
    if not receipt:
        return 0

    status = _int(receipt.get("status"), None) if receipt.get("status") is not None else None
    gas_used = _int(receipt.get("gasUsed"), 0)
    effective = _int(receipt.get("effectiveGasPrice"), 0)
    conn.execute(
        """UPDATE transactions
           SET status=?,gas_used=?,effective_gas_price_wei=?,receipt_scanned=1
           WHERE tx_hash=?""",
        (status, gas_used, str(effective), tx_hash),
    )

    inserted = 0
    for log in receipt.get("logs", []):
        topics = [str(x).lower() for x in log.get("topics", [])]
        if len(topics) < 3 or topics[0] != TRANSFER_TOPIC:
            continue
        token = (log.get("address") or "").lower()
        if not token:
            continue
        from_addr = _topic_addr(topics[1])
        to_addr = _topic_addr(topics[2])
        raw_amount = str(_int(log.get("data"), 0))
        log_index = _int(log.get("logIndex"), 0)
        conn.execute(
            """INSERT OR IGNORE INTO token_transfers(
                tx_hash,log_index,token,from_addr,to_addr,raw_amount
            ) VALUES(?,?,?,?,?,?)""",
            (tx_hash, log_index, token, from_addr, to_addr, raw_amount),
        )
        # Fetch metadata lazily. Failure is non-fatal.
        try:
            get_token_meta(conn, rpc, token, getattr(settings, "csv_dir", None), getattr(settings, "chain_id", None))
        except Exception:
            pass
        inserted += 1
    conn.commit()
    return inserted
