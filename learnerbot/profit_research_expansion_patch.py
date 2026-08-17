from __future__ import annotations

from . import analyser as _analyser
from . import sibot as _sibot
from .config import load_addresses
from .db import get_state, set_state
from .profit import analyse_tx
from .receipts import ingest_receipt

_ORIGINAL_SIBOT_ENSURE = _sibot.ensure_settings


def _int(v, default):
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _candidate_pool(conn, settings, pool_size: int):
    rows = list(conn.execute(
        """SELECT * FROM wallet_scores
           ORDER BY COALESCE(bot_score,0) DESC, COALESCE(tx_count,0) DESC
           LIMIT ?""",
        (int(pool_size),),
    ).fetchall())
    present = {str(r["wallet"]).lower() for r in rows}
    for address in load_addresses(settings.csv_dir, "wallet_watchlist.csv", settings.chain_id):
        a = str(address).lower()
        if a in present:
            continue
        row = conn.execute("SELECT * FROM wallet_scores WHERE wallet=?", (a,)).fetchone()
        if row:
            rows.append(row)
            present.add(a)
    return rows


def analyse_top_wallets(conn, rpc, settings, top: int = 20) -> dict:
    """Broad rotating profit research.

    Bot score now decides research priority only.  The final Top-20 remains based on
    measured P&L.  A large candidate pool is rotated in bounded batches so five
    chains do not attempt tens of thousands of receipt RPC calls every cycle.
    """
    general = settings.app.general()
    pool_size = max(100, min(5000, _int(general.get("profit_research_candidate_pool", 500), 500)))
    batch_size = max(int(top or 0), max(20, min(250, _int(general.get("profit_research_batch_per_cycle", 50), 50))))
    tx_limit = max(5, min(100, _int(general.get("profit_research_txs_per_wallet", 30), 30)))

    pool = _candidate_pool(conn, settings, pool_size)
    if not pool:
        return {"candidate_pool": 0, "wallets": 0, "transactions": 0, "transfer_logs": 0, "evidence_rows": 0, "cursor": 0}

    key = f"profit_research_cursor:{settings.chain_id}"
    cursor = _int(get_state(conn, key, "0"), 0) % len(pool)
    take = min(batch_size, len(pool))
    selected = [pool[(cursor + i) % len(pool)] for i in range(take)]
    next_cursor = (cursor + take) % len(pool)

    tx_analysed = 0
    transfer_logs = 0
    evidence = 0
    for w in selected:
        wallet = str(w["wallet"]).lower()
        executor = w["primary_executor"]
        txs = conn.execute(
            """SELECT tx_hash,receipt_scanned FROM transactions
               WHERE from_addr=?
               ORDER BY block_number DESC,tx_index DESC LIMIT ?""",
            (wallet, tx_limit),
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

    set_state(conn, key, str(next_cursor))
    try:
        proven_positive = int(conn.execute(
            """SELECT COUNT(*) n FROM (
                 SELECT wallet,SUM(COALESCE(net_base,0)) net
                 FROM profit_evidence
                 WHERE proof_quality='PROVEN_WRAPPED_BASE'
                 GROUP BY wallet HAVING net>0
               )"""
        ).fetchone()["n"])
    except Exception:
        proven_positive = 0

    return {
        "candidate_pool": len(pool),
        "wallets": len(selected),
        "transactions": tx_analysed,
        "transfer_logs": transfer_logs,
        "evidence_rows": evidence,
        "proven_positive_wallets": proven_positive,
        "cursor": next_cursor,
    }


def ensure_settings(app):
    """Increase the SiBot history universe without re-tightening existing user choices."""
    path = _ORIGINAL_SIBOT_ENSURE(app)
    rows = _sibot._rows(path)
    changed = False
    for row in rows:
        key = str(row.get("setting") or "").strip()
        current = str(row.get("value") or "").strip()
        if key == "history_candidate_wallets" and current in {"40", "100"}:
            row["value"] = "500"
            changed = True
    if changed:
        _sibot._atomic_csv(path, rows, ["chain_id", "setting", "value", "description"])
    return path


def install():
    if getattr(_analyser, "_broad_profit_research_installed", False):
        return
    _analyser.analyse_top_wallets = analyse_top_wallets
    _sibot.ensure_settings = ensure_settings
    _analyser._broad_profit_research_installed = True


install()
