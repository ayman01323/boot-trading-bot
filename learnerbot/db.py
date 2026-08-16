from __future__ import annotations
from typing import Optional

import sqlite3
from pathlib import Path

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS blocks (
    number INTEGER PRIMARY KEY,
    block_hash TEXT,
    timestamp INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_hash TEXT PRIMARY KEY,
    block_number INTEGER NOT NULL,
    tx_index INTEGER,
    from_addr TEXT NOT NULL,
    to_addr TEXT,
    selector TEXT,
    value_wei TEXT NOT NULL,
    gas_limit INTEGER,
    gas_price_wei TEXT,
    nonce INTEGER,
    input_len INTEGER,
    status INTEGER,
    gas_used INTEGER,
    effective_gas_price_wei TEXT,
    receipt_scanned INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(block_number) REFERENCES blocks(number)
);

CREATE INDEX IF NOT EXISTS idx_tx_from_block ON transactions(from_addr, block_number);
CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions(to_addr);
CREATE INDEX IF NOT EXISTS idx_tx_selector ON transactions(selector);

CREATE TABLE IF NOT EXISTS token_meta (
    token TEXT PRIMARY KEY,
    symbol TEXT,
    decimals INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS token_transfers (
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    token TEXT NOT NULL,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    raw_amount TEXT NOT NULL,
    PRIMARY KEY(tx_hash, log_index)
);

CREATE INDEX IF NOT EXISTS idx_transfer_tx ON token_transfers(tx_hash);
CREATE INDEX IF NOT EXISTS idx_transfer_token ON token_transfers(token);

CREATE TABLE IF NOT EXISTS wallet_scores (
    wallet TEXT PRIMARY KEY,
    tx_count INTEGER,
    first_ts INTEGER,
    last_ts INTEGER,
    tx_per_min REAL,
    repeat_to_ratio REAL,
    repeat_selector_ratio REAL,
    zero_value_ratio REAL,
    builder_tx_count INTEGER,
    bot_score REAL,
    primary_executor TEXT,
    updated_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wallet_bot_score ON wallet_scores(bot_score DESC);

CREATE TABLE IF NOT EXISTS profit_evidence (
    tx_hash TEXT NOT NULL,
    wallet TEXT NOT NULL,
    executor TEXT,
    base_token TEXT,
    base_symbol TEXT,
    gross_delta REAL,
    gas_bnb REAL,
    builder_payment_bnb REAL,
    net_base REAL,
    net_usd REAL,
    proof_quality TEXT NOT NULL,
    classification TEXT,
    route_fingerprint TEXT,
    created_at INTEGER,
    PRIMARY KEY(tx_hash, wallet)
);

CREATE TABLE IF NOT EXISTS strategy_patterns (
    pattern_id TEXT PRIMARY KEY,
    executor TEXT,
    selector TEXT,
    route_fingerprint TEXT,
    strategy_class TEXT,
    tx_count INTEGER,
    wallet_count INTEGER,
    proven_profit_count INTEGER,
    positive_count INTEGER,
    avg_net_base REAL,
    base_symbol TEXT,
    confidence REAL,
    replicability REAL,
    status TEXT,
    updated_at INTEGER
);


CREATE TABLE IF NOT EXISTS trade_behaviour_evidence (
    tx_hash TEXT NOT NULL,
    wallet TEXT NOT NULL,
    behaviour TEXT NOT NULL,
    behaviour_confidence REAL,
    profit_base REAL,
    profit_usd REAL,
    proof_quality TEXT,
    block_timestamp INTEGER,
    executor TEXT,
    selector TEXT,
    notes TEXT,
    updated_at INTEGER,
    PRIMARY KEY(tx_hash, wallet)
);

CREATE INDEX IF NOT EXISTS idx_behaviour_type
ON trade_behaviour_evidence(behaviour);

CREATE INDEX IF NOT EXISTS idx_behaviour_wallet
ON trade_behaviour_evidence(wallet, behaviour);

CREATE TABLE IF NOT EXISTS behaviour_rankings (
    behaviour TEXT PRIMARY KEY,
    evidence_count INTEGER,
    wallet_count INTEGER,
    proven_count INTEGER,
    positive_count INTEGER,
    negative_count INTEGER,
    total_net_base REAL,
    total_net_usd REAL,
    avg_net_base REAL,
    median_positive_net_base REAL,
    active_hours REAL,
    profit_per_hour_base REAL,
    profit_per_hour_usd REAL,
    median_seconds_between_positive REAL,
    positive_ratio REAL,
    profit_score REAL,
    speed_score REAL,
    consistency_score REAL,
    evidence_score REAL,
    overall_score REAL,
    rank_profit INTEGER,
    rank_speed INTEGER,
    rank_overall INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS wallet_behaviour_rankings (
    wallet TEXT NOT NULL,
    behaviour TEXT NOT NULL,
    evidence_count INTEGER,
    proven_count INTEGER,
    positive_count INTEGER,
    negative_count INTEGER,
    total_net_base REAL,
    total_net_usd REAL,
    avg_net_base REAL,
    active_hours REAL,
    profit_per_hour_base REAL,
    profit_per_hour_usd REAL,
    median_seconds_between_positive REAL,
    positive_ratio REAL,
    overall_score REAL,
    updated_at INTEGER,
    PRIMARY KEY(wallet, behaviour)
);

CREATE INDEX IF NOT EXISTS idx_wallet_behaviour_profit
ON wallet_behaviour_rankings(total_net_base DESC);

CREATE INDEX IF NOT EXISTS idx_wallet_behaviour_speed
ON wallet_behaviour_rankings(profit_per_hour_base DESC);


CREATE TABLE IF NOT EXISTS copy_wallet_candidates (
    wallet TEXT NOT NULL,
    behaviour TEXT NOT NULL,
    status TEXT NOT NULL,
    pass_checks INTEGER NOT NULL,
    copy_score REAL,
    bot_score REAL,
    avg_behaviour_confidence REAL,
    evidence_count INTEGER,
    proven_count INTEGER,
    positive_count INTEGER,
    negative_count INTEGER,
    positive_ratio REAL,
    total_net_base REAL,
    profit_per_hour_base REAL,
    active_hours REAL,
    avg_net_base REAL,
    max_positive_base REAL,
    max_loss_base REAL,
    median_seconds_between_positive REAL,
    rejection_reasons TEXT,
    updated_at INTEGER,
    PRIMARY KEY(wallet, behaviour)
);
CREATE INDEX IF NOT EXISTS idx_copy_candidates_score
ON copy_wallet_candidates(status, copy_score DESC);

CREATE TABLE IF NOT EXISTS copy_trade_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    wallet TEXT,
    behaviour TEXT,
    route_id TEXT,
    action TEXT NOT NULL,
    recommendation_mode TEXT NOT NULL,
    reason TEXT,
    source_input_base REAL,
    recommended_input_base REAL,
    expected_gross_profit_base REAL,
    captured_gross_profit_base REAL,
    estimated_gas_base REAL,
    builder_fee_base REAL,
    slippage_reserve_base REAL,
    conservative_net_profit_base REAL,
    signal_age_seconds REAL,
    checks_passed INTEGER,
    checks_failed INTEGER,
    check_summary TEXT,
    observed_at INTEGER,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_copy_rec_action
ON copy_trade_recommendations(action, created_at DESC);

CREATE TABLE IF NOT EXISTS etherscan_cache (
    wallet TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(wallet, kind)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection tuned for concurrent scanner + Telegram reads.

    - waits up to 30s instead of immediately failing on a short writer lock;
    - uses WAL mode so readers do not normally block the scanner writer;
    - retries one-time schema/WAL initialization if another connection is busy.
    """
    import time

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    last_exc = None
    for attempt in range(8):
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                conn.close()
                raise
            time.sleep(0.25 * (attempt + 1))

    conn.close()
    raise last_exc if last_exc is not None else RuntimeError("SQLite initialization failed")

def get_state(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
