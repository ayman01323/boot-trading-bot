from __future__ import annotations

from .receipts import ingest_receipt
from .profit import analyse_tx
from .config import load_addresses

def analyse_top_wallets(conn, rpc, settings, top: int = 20) -> dict:
    wallets = list(conn.execute(
        """SELECT * FROM wallet_scores
           WHERE bot_score>=?
           ORDER BY bot_score DESC LIMIT ?""",
        (settings.bot_score_threshold, top),
    ).fetchall())
    # Hot-reloaded watchlist entries are always eligible for deep analysis if already observed.
    watched = load_addresses(settings.csv_dir, "wallet_watchlist.csv", settings.chain_id)
    present = {w["wallet"] for w in wallets}
    for address in watched:
        if address in present:
            continue
        row = conn.execute("SELECT * FROM wallet_scores WHERE wallet=?", (address,)).fetchone()
        if row:
            wallets.append(row); present.add(address)
    tx_analysed = 0
    transfer_logs = 0
    evidence = 0

    for w in wallets:
        wallet = w["wallet"]
        executor = w["primary_executor"]
        txs = conn.execute(
            """SELECT tx_hash,receipt_scanned FROM transactions
               WHERE from_addr=?
               ORDER BY block_number DESC,tx_index DESC LIMIT ?""",
            (wallet, settings.analyse_txs_per_wallet),
        ).fetchall()
        for tx in txs:
            if not tx["receipt_scanned"]:
                try:
                    transfer_logs += ingest_receipt(conn, rpc, tx["tx_hash"], settings)
                except Exception:
                    continue
            try:
                analyse_tx(conn, settings, wallet, tx["tx_hash"], executor)
                evidence += 1
            except Exception:
                pass
            tx_analysed += 1
    return {
        "wallets": len(wallets),
        "transactions": tx_analysed,
        "transfer_logs": transfer_logs,
        "evidence_rows": evidence,
    }
