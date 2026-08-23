from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .config import load_chains, load_dex_registry, load_kv_scoped
from .fee_engine import ledger, master_wallet, profit_share_amount, user_fee_plan
from .live_executor import LiveTrader, LiveTradingError, V2_ROUTERS
from .multi_wallet_store import MultiWalletStore
from .product_universe import route_product_policy
from .telegram import send_message
from .user_registry import all_users, require_user, user_bool, user_setting

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().lower().removeprefix("0x")
SUCCESS_STATUSES = {"SUCCESS", "SUCCESS_FEE_PENDING"}

DEFAULTS = {
    "platform_enabled": ("true", "Master SiBot research/monitoring gate"),
    "history_fetch_days": ("365", "Remote history depth used to support the ranking window"),
    "history_refresh_hours": ("12", "Refresh a wallet's historical record after this many hours"),
    "history_candidate_wallets": ("40", "Candidate wallets per chain queued for historical reconstruction"),
    "history_max_pages": ("3", "Maximum Etherscan pages per history endpoint"),
    "history_page_size": ("10000", "Rows requested per Etherscan page"),
    "history_api_delay_seconds": ("0.22", "Delay between public history API calls"),
    "require_complete_history": ("true", "Only complete/reconciled histories may become SiMo leaders"),
    "lookback_days": ("60", "Default SiBot realised-PnL ranking window"),
    "top_wallets": ("20", "Number of profitable wallets retained per chain"),
    "leaders_per_chain": ("2", "Number of top-ranked SiMo leaders per chain"),
    "allocation_pct": ("20", "Percent of SiBot chain capital allocated to a new copied position"),
    "max_exposure_pct": ("60", "Maximum SiBot open principal exposure per chain"),
    "min_closed_trades": ("50", "Minimum matched BUY-to-SELL historical trades"),
    "min_win_rate_pct": ("55", "Minimum historical winning-trade percentage"),
    "max_signal_age_seconds": ("20", "Maximum age of a confirmed SiMo BUY before copying"),
    "max_entry_deterioration_pct": ("1.5", "Maximum worse entry price versus the SiMo leader"),
    "max_roundtrip_loss_pct": ("3", "Maximum current quote round-trip loss before entry"),
    "max_positions_per_chain": ("5", "Maximum simultaneous SiBot positions per chain"),
    "monitor_interval_seconds": ("3", "Leader block polling interval"),
    "position_interval_seconds": ("10", "Open-position PnL/exit monitoring interval"),
    "ranking_refresh_seconds": ("120", "Rebuild each user's Top-20 and SiMo leader set"),
    "history_worker_seconds": ("12", "Pause between bounded history backfill passes"),
    "stop_loss_pct": ("10", "Independent SiBot capital-protection stop loss"),
    "take_profit_pct": ("25", "Independent SiBot take-profit threshold"),
    "min_exit_profit_pct": ("0.10", "Minimum estimated net profit when following a leader exit"),
    "max_hold_hours": ("24", "Profit-taking time threshold for old positions"),
    "mirror_partial_sells": ("true", "Mirror a primary SiMo leader's partial sells"),
    "min_trade_native": ("0.0001", "Minimum native amount for a SiBot copied BUY"),
    "buy_gas_units": ("250000", "Conservative gas units used for SHADOW entry accounting"),
    "exit_gas_units": ("250000", "Conservative gas units used for exit decisions"),
    "require_auto_product_live": ("true", "Require current product-universe AUTO approval for LIVE SiBot entries"),
}

SETTING_SPECS = {
    "lookback_days": (1, 365, "days"),
    "leaders_per_chain": (1, 10, "leaders/chain"),
    "allocation_pct": (0.1, 100, "%"),
    "max_exposure_pct": (1, 100, "%"),
    "min_closed_trades": (1, 10000, "trades"),
    "min_win_rate_pct": (0, 100, "%"),
    "max_signal_age_seconds": (1, 300, "seconds"),
    "max_entry_deterioration_pct": (0, 25, "%"),
    "max_roundtrip_loss_pct": (0.1, 25, "%"),
    "max_positions_per_chain": (1, 50, "positions"),
    "stop_loss_pct": (0.1, 80, "%"),
    "take_profit_pct": (0.1, 500, "%"),
    "min_exit_profit_pct": (0, 100, "%"),
    "max_hold_hours": (1, 720, "hours"),
}

_DB_LOCK = threading.RLock()
_WORKERS_STARTED = False
_WORKER_LOCK = threading.Lock()
_REFRESH_NOW = set()


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _atomic_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows([{h: r.get(h, "") for h in headers} for r in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def ensure_settings(app) -> Path:
    path = Path(app.csv_dir) / "sibot_settings.csv"
    headers = ["chain_id", "setting", "value", "description"]
    rows = _rows(path)
    known = {(str(r.get("chain_id") or "*").strip(), str(r.get("setting") or "").strip()) for r in rows}
    changed = False
    for key, (value, description) in DEFAULTS.items():
        if ("*", key) not in known and ("0", key) not in known:
            rows.append({"chain_id": "*", "setting": key, "value": value, "description": description})
            changed = True
    if changed or not path.exists():
        _atomic_csv(path, rows, headers)
    return path


def platform_settings(app, chain_id=0) -> dict:
    ensure_settings(app)
    return load_kv_scoped(Path(app.csv_dir) / "sibot_settings.csv", int(chain_id or 0))


def user_settings(app, telegram_id, chain_id=0) -> dict:
    base = platform_settings(app, chain_id)
    out = dict(base)
    for key in DEFAULTS:
        v = user_setting(app.csv_dir, telegram_id, int(chain_id or 0), f"sibot_{key}", None)
        if v is not None:
            out[key] = str(v)
    out["enabled"] = str(user_setting(app.csv_dir, telegram_id, int(chain_id or 0), "sibot_enabled", "false"))
    out["auto_trade_enabled"] = str(user_setting(app.csv_dir, telegram_id, int(chain_id or 0), "sibot_auto_trade_enabled", "false"))
    return out


def db_path(app) -> Path:
    return Path(app.data_dir) / "sibot.sqlite3"


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS wallet_trades(
  trade_id TEXT PRIMARY KEY,
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  wallet TEXT NOT NULL,
  token TEXT NOT NULL,
  symbol TEXT,
  decimals INTEGER,
  buy_tx TEXT NOT NULL,
  sell_tx TEXT NOT NULL,
  buy_ts INTEGER NOT NULL,
  sell_ts INTEGER NOT NULL,
  token_amount_raw TEXT NOT NULL,
  cost_native TEXT NOT NULL,
  proceeds_native TEXT NOT NULL,
  buy_gas_native TEXT NOT NULL,
  sell_gas_native TEXT NOT NULL,
  net_native TEXT NOT NULL,
  source TEXT,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sibot_wallet_trades_rank ON wallet_trades(chain_id,wallet,sell_ts);

CREATE TABLE IF NOT EXISTS wallet_history_status(
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  wallet TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  coverage_start_ts INTEGER,
  coverage_end_ts INTEGER,
  history_complete INTEGER NOT NULL DEFAULT 0,
  unmatched_sells INTEGER NOT NULL DEFAULT 0,
  normal_rows INTEGER NOT NULL DEFAULT 0,
  token_rows INTEGER NOT NULL DEFAULT 0,
  internal_rows INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  PRIMARY KEY(chain_id,wallet)
);

CREATE TABLE IF NOT EXISTS rankings(
  telegram_id TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  lookback_days INTEGER NOT NULL,
  rank INTEGER NOT NULL,
  wallet TEXT NOT NULL,
  gross_profit_native TEXT NOT NULL,
  gross_loss_native TEXT NOT NULL,
  net_profit_native TEXT NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  closed_trades INTEGER NOT NULL,
  win_rate REAL NOT NULL,
  history_complete INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,chain_id,wallet)
);
CREATE INDEX IF NOT EXISTS idx_sibot_rankings ON rankings(telegram_id,chain_id,rank);

CREATE TABLE IF NOT EXISTS leaders(
  telegram_id TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  rank INTEGER NOT NULL,
  wallet TEXT NOT NULL,
  net_profit_native TEXT NOT NULL,
  win_rate REAL NOT NULL,
  closed_trades INTEGER NOT NULL,
  selected_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,chain_id,wallet)
);
CREATE INDEX IF NOT EXISTS idx_sibot_leaders ON leaders(chain_id,wallet);

CREATE TABLE IF NOT EXISTS leader_events(
  event_id TEXT PRIMARY KEY,
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  leader_wallet TEXT NOT NULL,
  tx_hash TEXT NOT NULL,
  action TEXT NOT NULL,
  token TEXT NOT NULL,
  symbol TEXT,
  decimals INTEGER,
  token_amount_raw TEXT NOT NULL,
  native_amount TEXT NOT NULL,
  sell_pct REAL,
  block_number INTEGER NOT NULL,
  event_ts INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sibot_events_leader ON leader_events(chain_id,leader_wallet,event_ts);

CREATE TABLE IF NOT EXISTS positions(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  wallet_id TEXT,
  wallet_address TEXT NOT NULL,
  chain_id INTEGER NOT NULL,
  chain_slug TEXT NOT NULL,
  primary_leader TEXT NOT NULL,
  leader_rank INTEGER,
  token TEXT NOT NULL,
  symbol TEXT,
  decimals INTEGER,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  token_amount_raw TEXT NOT NULL,
  entry_input_native TEXT NOT NULL,
  entry_cost_native TEXT NOT NULL,
  entry_tx TEXT,
  entry_ts INTEGER NOT NULL,
  leader_buy_tx TEXT,
  leader_entry_native TEXT,
  leader_entry_token_raw TEXT,
  current_exit_native TEXT,
  unrealised_net_native TEXT,
  unrealised_pct REAL,
  peak_unrealised_pct REAL,
  leader_exit_pending INTEGER NOT NULL DEFAULT 0,
  realised_net_native TEXT NOT NULL DEFAULT '0',
  realised_user_net_native TEXT NOT NULL DEFAULT '0',
  profit_fee_native TEXT NOT NULL DEFAULT '0',
  exit_tx TEXT,
  exit_reason TEXT,
  closed_at INTEGER,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sibot_positions_open ON positions(telegram_id,chain_id,status,token);

CREATE TABLE IF NOT EXISTS position_leaders(
  position_id TEXT NOT NULL,
  leader_wallet TEXT NOT NULL,
  primary_flag INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  buy_tx TEXT,
  joined_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(position_id,leader_wallet)
);

CREATE TABLE IF NOT EXISTS state(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def connect(app) -> sqlite3.Connection:
    p = db_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _state(conn, key, default=None):
    r = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _set_state(conn, key, value):
    conn.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def _chain(app, chain_id=None, slug=None):
    for c in load_chains(app, enabled_only=False):
        if chain_id is not None and int(c.chain_id) == int(chain_id):
            return c
        if slug is not None and c.slug == str(slug).lower():
            return c
    return None


def _routers(app, chain) -> set[str]:
    out = set()
    if chain.chain_id in V2_ROUTERS:
        out.add(V2_ROUTERS[chain.chain_id].lower())
    for r in load_dex_registry(app.csv_dir, chain.chain_id):
        a = str(r.get("router") or "").strip()
        if Web3.is_address(a):
            out.add(a.lower())
    return out


def _candidate_wallets(app, chain, limit: int) -> list[str]:
    core = Path(app.data_dir) / f"{chain.slug}.sqlite3"
    if not core.exists():
        return []
    conn = sqlite3.connect(core, timeout=10)
    conn.row_factory = sqlite3.Row
    out = []
    try:
        try:
            rows = conn.execute(
                """SELECT w.wallet,COALESCE(SUM(CASE WHEN p.classification IN
                       ('TOKEN_BUY_OR_ENTRY_CANDIDATE','TOKEN_SALE_OR_EXIT_CANDIDATE') THEN 1 ELSE 0 END),0) directional,
                       COALESCE(w.bot_score,0) bot_score
                   FROM wallet_scores w LEFT JOIN profit_evidence p ON p.wallet=w.wallet
                   GROUP BY w.wallet
                   ORDER BY directional DESC,bot_score DESC LIMIT ?""", (max(limit * 2, limit),)
            ).fetchall()
            for r in rows:
                a = str(r["wallet"] or "").lower()
                if Web3.is_address(a) and a not in out:
                    out.append(a)
        except sqlite3.Error:
            rows = conn.execute("SELECT wallet FROM wallet_scores ORDER BY bot_score DESC LIMIT ?", (max(limit * 2, limit),)).fetchall()
            for r in rows:
                a = str(r["wallet"] or "").lower()
                if Web3.is_address(a) and a not in out:
                    out.append(a)
    finally:
        conn.close()
    with closing(connect(app)) as sc:
        for r in sc.execute("SELECT wallet FROM wallet_history_status WHERE chain_id=? ORDER BY fetched_at DESC", (chain.chain_id,)).fetchall():
            a = str(r["wallet"] or "").lower()
            if Web3.is_address(a) and a not in out:
                out.append(a)
    return out[:limit]


def _etherscan_page(app, chain_id: int, action: str, wallet: str, page: int, offset: int) -> list[dict]:
    if not app.etherscan_api_key:
        raise RuntimeError("ETHERSCAN_API_KEY is not configured; SiBot cannot verify 60-day wallet histories")
    params = {
        "chainid": str(chain_id), "module": "account", "action": action,
        "address": wallet, "startblock": 0, "endblock": 999999999,
        "page": int(page), "offset": int(offset), "sort": "desc", "apikey": app.etherscan_api_key,
    }
    r = requests.get(ETHERSCAN_V2, params=params, timeout=30, headers={"User-Agent": "BOOT-SiBot/1.0"})
    r.raise_for_status()
    payload = r.json()
    result = payload.get("result")
    if payload.get("status") == "0":
        text = f"{payload.get('message')} {result}"
        if "No transactions found" in text or result == []:
            return []
        raise RuntimeError(f"Etherscan {action}: {text[:300]}")
    return result if isinstance(result, list) else []


def _fetch_action(app, chain_id, action, wallet, cutoff_ts, max_pages, page_size, delay) -> tuple[list[dict], bool]:
    rows = []
    reached = False
    for page in range(1, max_pages + 1):
        batch = _etherscan_page(app, chain_id, action, wallet, page, page_size)
        rows.extend(batch)
        if not batch:
            reached = True
            break
        ts = [_int(x.get("timeStamp"), 0) for x in batch if _int(x.get("timeStamp"), 0) > 0]
        if ts and min(ts) <= cutoff_ts:
            reached = True
            break
        if len(batch) < page_size:
            reached = True
            break
        time.sleep(max(0.0, delay))
    return rows, reached


def _successful_normal(row: dict) -> bool:
    if str(row.get("isError") or "0") == "1":
        return False
    st = str(row.get("txreceipt_status") or "1")
    return st not in {"0", "false", "False"}


def reconstruct_spot_trades(wallet: str, routers: set[str], normal_rows: list[dict], token_rows: list[dict], internal_rows: list[dict], chain_id: int, chain_slug: str) -> tuple[list[dict], int]:
    """Reconstruct only direct native<->ERC20 DEX positions and match them FIFO.

    This intentionally ignores token transfers, LP operations, wrapped-base-only swaps,
    bridges and unmatched inventory. The resulting history is narrower than a generic
    wallet PnL service but corresponds to the class of directional trade SiBot can copy.
    """
    w = wallet.lower()
    normals = {str(r.get("hash") or "").lower(): r for r in normal_rows if _successful_normal(r) and str(r.get("from") or "").lower() == w}
    flows = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0, "symbol": "", "decimals": 18}))
    for r in token_rows:
        h = str(r.get("hash") or "").lower()
        if h not in normals:
            continue
        token = str(r.get("contractAddress") or "").lower()
        if not Web3.is_address(token):
            continue
        raw = _int(r.get("value"), 0)
        if raw <= 0:
            continue
        f = str(r.get("from") or "").lower(); t = str(r.get("to") or "").lower()
        x = flows[h][token]
        x["symbol"] = str(r.get("tokenSymbol") or token[:10])[:32]
        x["decimals"] = max(0, min(36, _int(r.get("tokenDecimal"), 18)))
        if t == w and f != w:
            x["in"] += raw
        if f == w and t != w:
            x["out"] += raw
    internal_in = defaultdict(Decimal)
    for r in internal_rows:
        if str(r.get("isError") or "0") == "1":
            continue
        if str(r.get("to") or "").lower() != w:
            continue
        h = str(r.get("hash") or "").lower()
        if h in normals:
            internal_in[h] += Decimal(str(_int(r.get("value"), 0))) / Decimal(10**18)

    events = []
    for h, tx in normals.items():
        to = str(tx.get("to") or "").lower()
        if routers and to not in routers:
            continue
        ts = _int(tx.get("timeStamp"), 0)
        value = Decimal(str(_int(tx.get("value"), 0))) / Decimal(10**18)
        gas = Decimal(str(_int(tx.get("gasUsed"), 0))) * Decimal(str(_int(tx.get("gasPrice"), 0))) / Decimal(10**18)
        token_items = []
        for token, f in flows.get(h, {}).items():
            net = int(f["in"]) - int(f["out"])
            if net:
                token_items.append((token, net, f))
        positive = [x for x in token_items if x[1] > 0]
        negative = [x for x in token_items if x[1] < 0]
        if value > 0 and len(positive) == 1 and not negative:
            token, raw, meta = positive[0]
            refund = internal_in.get(h, Decimal(0))
            principal = max(Decimal(0), value - refund)
            if principal > 0:
                events.append({"kind": "BUY", "tx": h, "ts": ts, "token": token, "raw": int(raw), "symbol": meta["symbol"], "decimals": meta["decimals"], "principal": principal, "gas": gas})
        elif value == 0 and len(negative) == 1 and not positive and internal_in.get(h, Decimal(0)) > 0:
            token, raw, meta = negative[0]
            events.append({"kind": "SELL", "tx": h, "ts": ts, "token": token, "raw": abs(int(raw)), "symbol": meta["symbol"], "decimals": meta["decimals"], "principal": internal_in[h], "gas": gas})
    events.sort(key=lambda x: (x["ts"], x["tx"]))

    lots = defaultdict(deque)
    out = []
    unmatched_sells = 0
    match_i = 0
    for ev in events:
        token = ev["token"]
        if ev["kind"] == "BUY":
            lots[token].append({**ev, "remaining": int(ev["raw"]), "remaining_cost": ev["principal"] + ev["gas"]})
            continue
        remaining = int(ev["raw"])
        original = max(1, remaining)
        while remaining > 0 and lots[token]:
            lot = lots[token][0]
            qty = min(remaining, int(lot["remaining"]))
            buy_fraction = Decimal(qty) / Decimal(max(1, int(lot["remaining"])))
            cost = lot["remaining_cost"] * buy_fraction
            sell_fraction = Decimal(qty) / Decimal(original)
            proceeds = ev["principal"] * sell_fraction
            sell_gas = ev["gas"] * sell_fraction
            net = proceeds - sell_gas - cost
            match_i += 1
            trade_id = hashlib.sha256(f"{chain_id}|{w}|{lot['tx']}|{ev['tx']}|{token}|{match_i}".encode()).hexdigest()[:32]
            out.append({
                "trade_id": trade_id, "chain_id": chain_id, "chain_slug": chain_slug, "wallet": w,
                "token": token, "symbol": ev["symbol"] or lot["symbol"], "decimals": ev["decimals"],
                "buy_tx": lot["tx"], "sell_tx": ev["tx"], "buy_ts": lot["ts"], "sell_ts": ev["ts"],
                "token_amount_raw": str(qty), "cost_native": str(cost), "proceeds_native": str(proceeds),
                "buy_gas_native": str(lot["gas"] * buy_fraction), "sell_gas_native": str(sell_gas),
                "net_native": str(net), "source": "ETHERSCAN_DIRECT_NATIVE_FIFO", "updated_at": int(time.time()),
            })
            lot["remaining"] -= qty
            lot["remaining_cost"] -= cost
            remaining -= qty
            if lot["remaining"] <= 0:
                lots[token].popleft()
        if remaining > 0:
            unmatched_sells += 1
    return out, unmatched_sells


def refresh_wallet_history(app, chain, wallet: str) -> dict:
    cfg = platform_settings(app, chain.chain_id)
    fetch_days = max(30, min(3650, _int(cfg.get("history_fetch_days"), 365)))
    cutoff = int(time.time()) - fetch_days * 86400
    max_pages = max(1, min(20, _int(cfg.get("history_max_pages"), 3)))
    page_size = max(100, min(10000, _int(cfg.get("history_page_size"), 10000)))
    delay = max(0.0, min(2.0, _float(cfg.get("history_api_delay_seconds"), .22)))
    fetched_at = int(time.time())
    try:
        normal, c1 = _fetch_action(app, chain.chain_id, "txlist", wallet, cutoff, max_pages, page_size, delay)
        time.sleep(delay)
        token, c2 = _fetch_action(app, chain.chain_id, "tokentx", wallet, cutoff, max_pages, page_size, delay)
        time.sleep(delay)
        internal, c3 = _fetch_action(app, chain.chain_id, "txlistinternal", wallet, cutoff, max_pages, page_size, delay)
        trades, unmatched = reconstruct_spot_trades(wallet, _routers(app, chain), normal, token, internal, chain.chain_id, chain.slug)
        timestamps = [_int(r.get("timeStamp"), 0) for r in normal + token + internal if _int(r.get("timeStamp"), 0)]
        coverage_start = min(timestamps) if timestamps else fetched_at
        coverage_end = max(timestamps) if timestamps else fetched_at
        complete = bool(c1 and c2 and c3 and unmatched == 0)
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute("DELETE FROM wallet_trades WHERE chain_id=? AND wallet=?", (chain.chain_id, wallet.lower()))
            for r in trades:
                conn.execute("""INSERT INTO wallet_trades(trade_id,chain_id,chain_slug,wallet,token,symbol,decimals,buy_tx,sell_tx,buy_ts,sell_ts,token_amount_raw,cost_native,proceeds_native,buy_gas_native,sell_gas_native,net_native,source,updated_at)
                                VALUES(:trade_id,:chain_id,:chain_slug,:wallet,:token,:symbol,:decimals,:buy_tx,:sell_tx,:buy_ts,:sell_ts,:token_amount_raw,:cost_native,:proceeds_native,:buy_gas_native,:sell_gas_native,:net_native,:source,:updated_at)""", r)
            conn.execute("""INSERT INTO wallet_history_status(chain_id,chain_slug,wallet,fetched_at,coverage_start_ts,coverage_end_ts,history_complete,unmatched_sells,normal_rows,token_rows,internal_rows,error)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chain_id,wallet) DO UPDATE SET chain_slug=excluded.chain_slug,fetched_at=excluded.fetched_at,coverage_start_ts=excluded.coverage_start_ts,coverage_end_ts=excluded.coverage_end_ts,history_complete=excluded.history_complete,unmatched_sells=excluded.unmatched_sells,normal_rows=excluded.normal_rows,token_rows=excluded.token_rows,internal_rows=excluded.internal_rows,error=excluded.error""",
                         (chain.chain_id, chain.slug, wallet.lower(), fetched_at, coverage_start, coverage_end, 1 if complete else 0, unmatched, len(normal), len(token), len(internal), ""))
            conn.commit()
        return {"wallet": wallet, "trades": len(trades), "complete": complete, "unmatched_sells": unmatched}
    except Exception as exc:
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute("""INSERT INTO wallet_history_status(chain_id,chain_slug,wallet,fetched_at,history_complete,error)
                            VALUES(?,?,?,?,0,?) ON CONFLICT(chain_id,wallet) DO UPDATE SET fetched_at=excluded.fetched_at,history_complete=0,error=excluded.error""",
                         (chain.chain_id, chain.slug, wallet.lower(), fetched_at, f"{type(exc).__name__}: {str(exc)[:500]}"))
            conn.commit()
        return {"wallet": wallet, "trades": 0, "complete": False, "error": str(exc)}


def _next_history_wallet(app, chain) -> str | None:
    cfg = platform_settings(app, chain.chain_id)
    limit = max(20, min(500, _int(cfg.get("history_candidate_wallets"), 40)))
    refresh_after = max(1, _int(cfg.get("history_refresh_hours"), 12)) * 3600
    now = int(time.time())
    candidates = _candidate_wallets(app, chain, limit)
    if not candidates:
        return None
    with closing(connect(app)) as conn:
        status = {str(r["wallet"]).lower(): int(r["fetched_at"] or 0) for r in conn.execute("SELECT wallet,fetched_at FROM wallet_history_status WHERE chain_id=?", (chain.chain_id,)).fetchall()}
    candidates.sort(key=lambda a: status.get(a.lower(), 0))
    for wallet in candidates:
        if wallet in _REFRESH_NOW or now - status.get(wallet.lower(), 0) >= refresh_after:
            _REFRESH_NOW.discard(wallet)
            return wallet
    return None


def refresh_rankings(app, telegram_id, chain) -> list[dict]:
    cfg = user_settings(app, telegram_id, chain.chain_id)
    lookback = max(1, min(365, _int(cfg.get("lookback_days"), 60)))
    topn = max(1, min(100, _int(cfg.get("top_wallets"), 20)))
    min_closed = max(1, _int(cfg.get("min_closed_trades"), 50))
    min_win_rate = max(0.0, min(100.0, _float(cfg.get("min_win_rate_pct"), 55)))
    require_complete = _bool(cfg.get("require_complete_history"), True)
    cutoff = int(time.time()) - lookback * 86400
    now = int(time.time())
    with _DB_LOCK, closing(connect(app)) as conn:
        trades = conn.execute("SELECT wallet,net_native FROM wallet_trades WHERE chain_id=? AND sell_ts>=?", (chain.chain_id, cutoff)).fetchall()
        complete_map = {str(r["wallet"]).lower(): bool(r["history_complete"]) for r in conn.execute("SELECT wallet,history_complete FROM wallet_history_status WHERE chain_id=?", (chain.chain_id,)).fetchall()}
        agg = defaultdict(lambda: {"profit": Decimal(0), "loss": Decimal(0), "net": Decimal(0), "wins": 0, "losses": 0, "closed": 0})
        for r in trades:
            a = agg[str(r["wallet"]).lower()]
            net = _dec(r["net_native"])
            a["net"] += net; a["closed"] += 1
            if net > 0:
                a["profit"] += net; a["wins"] += 1
            elif net < 0:
                a["loss"] += -net; a["losses"] += 1
        qualified = []
        for wallet, a in agg.items():
            wr = (a["wins"] / a["closed"] * 100.0) if a["closed"] else 0.0
            complete = complete_map.get(wallet, False)
            if not (a["net"] > 0 and a["profit"] > a["loss"]):
                continue
            if a["closed"] < min_closed or wr < min_win_rate:
                continue
            if require_complete and not complete:
                continue
            qualified.append({"wallet": wallet, **a, "win_rate": wr, "history_complete": complete})
        qualified.sort(key=lambda x: (x["net"], x["profit"], x["win_rate"], x["closed"]), reverse=True)
        qualified = qualified[:topn]
        conn.execute("DELETE FROM rankings WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id))
        for i, a in enumerate(qualified, 1):
            conn.execute("""INSERT INTO rankings(telegram_id,chain_id,chain_slug,lookback_days,rank,wallet,gross_profit_native,gross_loss_native,net_profit_native,wins,losses,closed_trades,win_rate,history_complete,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (str(telegram_id), chain.chain_id, chain.slug, lookback, i, a["wallet"], str(a["profit"]), str(a["loss"]), str(a["net"]), a["wins"], a["losses"], a["closed"], a["win_rate"], 1 if a["history_complete"] else 0, now))
        old = {str(r["wallet"]).lower(): int(r["selected_at"] or now) for r in conn.execute("SELECT wallet,selected_at FROM leaders WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id)).fetchall()}
        conn.execute("DELETE FROM leaders WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id))
        nleaders = max(1, min(10, _int(cfg.get("leaders_per_chain"), 2)))
        for i, a in enumerate(qualified[:nleaders], 1):
            conn.execute("""INSERT INTO leaders(telegram_id,chain_id,chain_slug,rank,wallet,net_profit_native,win_rate,closed_trades,selected_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (str(telegram_id), chain.chain_id, chain.slug, i, a["wallet"], str(a["net"]), a["win_rate"], a["closed"], old.get(a["wallet"], now), now))
        conn.commit()
    export_rankings(app)
    return ranking_rows(app, telegram_id, chain.chain_id)


def refresh_all_rankings(app, telegram_id=None) -> None:
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if (u.get("status") or "").upper() == "ACTIVE"]
    if telegram_id is not None:
        users = [u for u in users if str(u.get("telegram_id")) == str(telegram_id)]
    for u in users:
        tid = str(u.get("telegram_id") or "")
        if not tid:
            continue
        for c in load_chains(app, enabled_only=True):
            try:
                require_user(app.csv_dir, tid, active=False, chain_slug=c.slug)
                refresh_rankings(app, tid, c)
            except Exception:
                continue


def ranking_rows(app, telegram_id, chain_id=None) -> list[dict]:
    with closing(connect(app)) as conn:
        if chain_id is None:
            rows = conn.execute("SELECT * FROM rankings WHERE telegram_id=? ORDER BY chain_id,rank", (str(telegram_id),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rankings WHERE telegram_id=? AND chain_id=? ORDER BY rank", (str(telegram_id), int(chain_id))).fetchall()
        return [dict(r) for r in rows]


def leader_rows(app, telegram_id, chain_id=None) -> list[dict]:
    with closing(connect(app)) as conn:
        if chain_id is None:
            rows = conn.execute("SELECT * FROM leaders WHERE telegram_id=? ORDER BY chain_id,rank", (str(telegram_id),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leaders WHERE telegram_id=? AND chain_id=? ORDER BY rank", (str(telegram_id), int(chain_id))).fetchall()
        return [dict(r) for r in rows]


def export_rankings(app) -> None:
    outdir = Path(app.csv_dir) / "auto"; outdir.mkdir(parents=True, exist_ok=True)
    with closing(connect(app)) as conn:
        rr = [dict(r) for r in conn.execute("SELECT * FROM rankings ORDER BY telegram_id,chain_id,rank").fetchall()]
        ll = [dict(r) for r in conn.execute("SELECT * FROM leaders ORDER BY telegram_id,chain_id,rank").fetchall()]
    _atomic_csv(outdir / "sibot_top20.csv", rr, list(rr[0].keys()) if rr else ["telegram_id","chain_id","chain_slug","lookback_days","rank","wallet","gross_profit_native","gross_loss_native","net_profit_native","wins","losses","closed_trades","win_rate","history_complete","updated_at"])
    _atomic_csv(outdir / "simo_leaders.csv", ll, list(ll[0].keys()) if ll else ["telegram_id","chain_id","chain_slug","rank","wallet","net_profit_native","win_rate","closed_trades","selected_at","updated_at"])


def request_history_refresh(app, telegram_id=None) -> None:
    for c in load_chains(app, enabled_only=True):
        for w in _candidate_wallets(app, c, max(20, _int(platform_settings(app, c.chain_id).get("history_candidate_wallets"), 40))):
            _REFRESH_NOW.add(w.lower())
    if telegram_id is not None:
        refresh_all_rankings(app, telegram_id)


def _rpc(chain):
    last = None
    for url in chain.rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 12}))
            if chain.chain_id in {56, 137}:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected() and int(w3.eth.chain_id) == int(chain.chain_id):
                return w3
        except Exception as exc:
            last = exc
    raise RuntimeError(f"No working RPC for {chain.slug}: {last or 'not configured'}")


def _topic_addr(topic) -> str:
    h = topic.hex() if hasattr(topic, "hex") else str(topic)
    h = h[2:] if h.startswith("0x") else h
    return "0x" + h[-40:].lower()


def _data_int(data) -> int:
    if isinstance(data, (bytes, bytearray)):
        return int.from_bytes(data, "big")
    h = data.hex() if hasattr(data, "hex") else str(data)
    return int(h, 16) if h else 0


def _transfer_deltas(receipt, wallet: str) -> dict[str, int]:
    w = wallet.lower(); out = defaultdict(int)
    for log in receipt.get("logs", []):
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
        if not t0.startswith("0x"):
            t0 = "0x" + t0
        if t0 != TRANSFER_TOPIC:
            continue
        token = str(log.get("address") or "").lower()
        frm = _topic_addr(topics[1]); to = _topic_addr(topics[2]); raw = _data_int(log.get("data"))
        if frm == w and to != w:
            out[token] -= raw
        elif to == w and frm != w:
            out[token] += raw
    return dict(out)


def _token_meta(w3, token: str) -> tuple[str, int]:
    abi = [
        {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
        {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"string"}]},
        {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    ]
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=abi)
    try: dec = int(c.functions.decimals().call())
    except Exception: dec = 18
    try: sym = str(c.functions.symbol().call())[:24]
    except Exception: sym = token[:10]
    return sym, max(0, min(36, dec))


def _leader_set(app, chain_id) -> dict[str, int]:
    with closing(connect(app)) as conn:
        rows = conn.execute("SELECT wallet,MIN(selected_at) selected_at FROM leaders WHERE chain_id=? GROUP BY wallet", (int(chain_id),)).fetchall()
        return {str(r["wallet"]).lower(): int(r["selected_at"] or 0) for r in rows}


def _record_event(app, chain, leader, tx, receipt, timestamp, w3=None) -> list[dict]:
    deltas = _transfer_deltas(receipt, leader)
    wrapped = str(chain.wrapped_base_address or "").lower()
    token_deltas = [(t, d) for t, d in deltas.items() if t != wrapped and d != 0]
    positive = [(t, d) for t, d in token_deltas if d > 0]
    negative = [(t, -d) for t, d in token_deltas if d < 0]
    value = Decimal(int(tx.get("value") or 0)) / Decimal(10**18)
    events = []
    if value > 0 and len(positive) == 1 and not negative:
        token, raw = positive[0]; action = "BUY"; native = value; sell_pct = None
    elif value == 0 and len(negative) == 1 and not positive:
        token, raw = negative[0]; action = "SELL"; native = Decimal(0); sell_pct = None
        try:
            abi = [{"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]}]
            c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=abi) if w3 is not None else None
            remaining = int(c.functions.balanceOf(Web3.to_checksum_address(leader)).call()) if c else 0
            sell_pct = float(Decimal(raw) * Decimal(100) / Decimal(max(1, raw + remaining)))
        except Exception:
            sell_pct = 100.0
    else:
        return []
    try:
        if w3 is None:
            w3 = _rpc(chain)
        sym, dec = _token_meta(w3, token)
    except Exception:
        sym, dec = token[:10], 18
    txh = tx.get("hash")
    txh = txh.hex() if hasattr(txh, "hex") else str(txh)
    eid = hashlib.sha256(f"{chain.chain_id}|{leader}|{txh}|{action}|{token}".encode()).hexdigest()[:32]
    row = {"event_id": eid, "chain_id": chain.chain_id, "chain_slug": chain.slug, "leader_wallet": leader.lower(), "tx_hash": txh,
           "action": action, "token": token, "symbol": sym, "decimals": dec, "token_amount_raw": str(raw), "native_amount": str(native),
           "sell_pct": sell_pct, "block_number": int(receipt.get("blockNumber") or tx.get("blockNumber") or 0), "event_ts": int(timestamp), "created_at": int(time.time())}
    with _DB_LOCK, closing(connect(app)) as conn:
        before = conn.total_changes
        conn.execute("""INSERT OR IGNORE INTO leader_events(event_id,chain_id,chain_slug,leader_wallet,tx_hash,action,token,symbol,decimals,token_amount_raw,native_amount,sell_pct,block_number,event_ts,created_at)
                        VALUES(:event_id,:chain_id,:chain_slug,:leader_wallet,:tx_hash,:action,:token,:symbol,:decimals,:token_amount_raw,:native_amount,:sell_pct,:block_number,:event_ts,:created_at)""", row)
        conn.commit(); inserted = conn.total_changes > before
    return [row] if inserted else []


def poll_leader_blocks(app, chain) -> list[dict]:
    leaders = _leader_set(app, chain.chain_id)
    if not leaders:
        return []
    w3 = _rpc(chain); latest = int(w3.eth.block_number)
    with closing(connect(app)) as conn:
        key = f"leader_last_block:{chain.chain_id}"
        last = _int(_state(conn, key, 0), 0)
        if last <= 0:
            _set_state(conn, key, latest)
            return []
        start = last + 1
        if latest - start > 20:
            start = max(start, latest - 20)
    routers = _routers(app, chain); events = []
    for bn in range(start, latest + 1):
        try:
            block = w3.eth.get_block(bn, full_transactions=True); ts = int(block.get("timestamp") or time.time())
        except Exception:
            continue
        for tx in block.get("transactions", []):
            frm = str(tx.get("from") or "").lower(); to = str(tx.get("to") or "").lower()
            if frm not in leaders or (routers and to not in routers) or ts < leaders[frm]:
                continue
            try:
                receipt = w3.eth.get_transaction_receipt(tx.get("hash"))
                if int(receipt.get("status") or 0) != 1:
                    continue
                events.extend(_record_event(app, chain, frm, tx, receipt, ts, w3=w3))
            except Exception:
                continue
    with closing(connect(app)) as conn:
        _set_state(conn, f"leader_last_block:{chain.chain_id}", latest)
    for ev in events:
        process_leader_event(app, ev)
    return events


def _gate_live(app, telegram_id, chain) -> tuple[bool, str]:
    try:
        u = require_user(app.csv_dir, telegram_id, active=True, chain_slug=chain.slug)
    except Exception as exc:
        return False, str(exc)
    if not _bool(u.get("can_auto_trade"), True):
        return False, "automatic trading is disabled for this account"
    store = MultiWalletStore(app.data_dir, app.csv_dir)
    if not store.has_wallet(telegram_id):
        return False, "no trading wallet"
    if not user_bool(app.csv_dir, telegram_id, chain.chain_id, "live_trading_enabled", False):
        return False, "user LIVE signing switch is OFF"
    if not _bool(load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", chain.chain_id).get("trading_enabled"), False):
        return False, "platform LIVE gate is OFF"
    if not _bool(load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", chain.chain_id).get("auto_trading_enabled"), False):
        return False, "platform AUTO gate is OFF"
    plan = user_fee_plan(app.csv_dir, telegram_id) or {}
    if _dec(plan.get("profit_share_bps"), 0) > 0 and not master_wallet(app.csv_dir, chain.chain_id):
        return False, "profit-share plan has no master fee wallet on this chain"
    return True, "PASS"


def can_start_live(app, telegram_id) -> tuple[bool, str]:
    require_user(app.csv_dir, telegram_id, active=True)
    if not MultiWalletStore(app.data_dir, app.csv_dir).has_wallet(telegram_id):
        return False, "Create or import a trading wallet first."
    if not user_bool(app.csv_dir, telegram_id, 0, "live_trading_enabled", False):
        return False, "Enable /live on CONFIRM first."
    if not _bool(load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", 0).get("trading_enabled"), False):
        return False, "The MASTER platform LIVE gate is OFF."
    if not _bool(load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0).get("auto_trading_enabled"), False):
        return False, "The MASTER platform AUTO gate is OFF."
    return True, "PASS"


def _position_size(app, telegram_id, trader: LiveTrader, cfg: dict) -> Decimal:
    reserve = _dec(trader.settings.get("min_native_gas_reserve"), "0.005")
    native = max(Decimal(0), trader.native_balance() - reserve)
    with closing(connect(app)) as conn:
        rows = conn.execute("SELECT entry_input_native FROM positions WHERE telegram_id=? AND chain_id=? AND status='OPEN'", (str(telegram_id), trader.chain.chain_id)).fetchall()
    open_principal = sum((_dec(r["entry_input_native"]) for r in rows), Decimal(0))
    chain_capital = native + open_principal
    allocation = max(Decimal(0), min(Decimal(1), _dec(cfg.get("allocation_pct"), 20) / Decimal(100)))
    exposure_limit = max(Decimal(0), min(Decimal(1), _dec(cfg.get("max_exposure_pct"), 60) / Decimal(100)))
    amount = chain_capital * allocation
    exposure_room = max(Decimal(0), chain_capital * exposure_limit - open_principal)
    amount = min(amount, exposure_room, native)
    try: max_input = _dec(trader.settings.get("max_native_input_per_trade"), "0.05")
    except Exception: max_input = Decimal("0.05")
    amount = min(amount, max_input)
    minimum = _dec(cfg.get("min_trade_native"), "0.0001")
    return amount if amount >= minimum else Decimal(0)


def _estimated_gas_native(trader: LiveTrader, cfg: dict, side="SELL") -> Decimal:
    units = _int(cfg.get("exit_gas_units" if side == "SELL" else "buy_gas_units"), 250000)
    try: gp = Decimal(int(trader.w3.eth.gas_price)) / Decimal(10**18)
    except Exception: gp = Decimal(0)
    try: bid = _dec(trader.settings.get("gas_bid_multiplier"), "1.25")
    except Exception: bid = Decimal("1.25")
    return Decimal(max(21000, units)) * gp * max(Decimal(1), bid)


def _validate_entry(app, trader: LiveTrader, event: dict, amount: Decimal, cfg: dict, live: bool) -> tuple[bool, str, dict]:
    age = max(0, int(time.time()) - _int(event.get("event_ts"), 0))
    if age > _int(cfg.get("max_signal_age_seconds"), 20):
        return False, f"stale leader signal ({age}s)", {}
    token = Web3.to_checksum_address(event["token"])
    if live and _bool(cfg.get("require_auto_product_live"), True):
        pol = route_product_policy(app.csv_dir, trader.chain.chain_id, [trader.wrapped, token, trader.wrapped])
        if not pol.get("auto_trade"):
            return False, f"product policy: {pol.get('reason')}", {}
    try:
        q = trader.quote_buy(token, amount)
    except Exception as exc:
        return False, f"buy quote failed: {exc}", {}
    leader_native = _dec(event.get("native_amount"), 0)
    leader_raw = Decimal(str(_int(event.get("token_amount_raw"), 0)))
    leader_dec = max(0, _int(event.get("decimals"), 18))
    leader_tokens = leader_raw / (Decimal(10) ** leader_dec)
    our_tokens = _dec(q.expected_out_human, 0)
    deterioration = Decimal(0)
    if leader_native > 0 and leader_tokens > 0 and our_tokens > 0:
        leader_price = leader_native / leader_tokens
        our_price = amount / our_tokens
        deterioration = (our_price / leader_price - Decimal(1)) * Decimal(100)
        if deterioration > _dec(cfg.get("max_entry_deterioration_pct"), "1.5"):
            return False, f"entry deterioration {deterioration:.3f}% exceeds limit", {"quote": q, "deterioration_pct": deterioration}
    expected_raw = int(_dec(q.expected_out_human) * (Decimal(10) ** int(q.token_decimals)))
    try:
        back = int(trader.router.functions.getAmountsOut(expected_raw, [token, trader.wrapped]).call()[-1])
        back_native = Decimal(back) / Decimal(10**18)
        roundtrip_loss = max(Decimal(0), (Decimal(1) - back_native / amount) * Decimal(100)) if amount > 0 else Decimal(100)
    except Exception as exc:
        return False, f"sellability/round-trip quote failed: {exc}", {"quote": q}
    if roundtrip_loss > _dec(cfg.get("max_roundtrip_loss_pct"), "3"):
        return False, f"round-trip loss {roundtrip_loss:.3f}% exceeds limit", {"quote": q, "deterioration_pct": deterioration, "roundtrip_loss_pct": roundtrip_loss}
    return True, "PASS", {"quote": q, "expected_raw": expected_raw, "deterioration_pct": deterioration, "roundtrip_loss_pct": roundtrip_loss}


def _open_position_for_token(app, telegram_id, chain_id, token) -> dict | None:
    with closing(connect(app)) as conn:
        r = conn.execute("SELECT * FROM positions WHERE telegram_id=? AND chain_id=? AND lower(token)=? AND status='OPEN' ORDER BY entry_ts LIMIT 1", (str(telegram_id), int(chain_id), str(token).lower())).fetchone()
        return dict(r) if r else None


def _attach_leader(app, position_id, leader_wallet, buy_tx, primary=False):
    now = int(time.time())
    with _DB_LOCK, closing(connect(app)) as conn:
        conn.execute("""INSERT INTO position_leaders(position_id,leader_wallet,primary_flag,active,buy_tx,joined_at,updated_at)
                        VALUES(?,?,?,?,?,?,?) ON CONFLICT(position_id,leader_wallet) DO UPDATE SET active=1,buy_tx=excluded.buy_tx,updated_at=excluded.updated_at""",
                     (position_id, leader_wallet.lower(), 1 if primary else 0, 1, buy_tx, now, now))
        conn.commit()


def _insert_position(app, row: dict):
    with _DB_LOCK, closing(connect(app)) as conn:
        conn.execute("""INSERT INTO positions(position_id,telegram_id,wallet_id,wallet_address,chain_id,chain_slug,primary_leader,leader_rank,token,symbol,decimals,mode,status,token_amount_raw,entry_input_native,entry_cost_native,entry_tx,entry_ts,leader_buy_tx,leader_entry_native,leader_entry_token_raw,current_exit_native,unrealised_net_native,unrealised_pct,peak_unrealised_pct,leader_exit_pending,realised_net_native,realised_user_net_native,profit_fee_native,exit_tx,exit_reason,closed_at,updated_at)
                        VALUES(:position_id,:telegram_id,:wallet_id,:wallet_address,:chain_id,:chain_slug,:primary_leader,:leader_rank,:token,:symbol,:decimals,:mode,:status,:token_amount_raw,:entry_input_native,:entry_cost_native,:entry_tx,:entry_ts,:leader_buy_tx,:leader_entry_native,:leader_entry_token_raw,:current_exit_native,:unrealised_net_native,:unrealised_pct,:peak_unrealised_pct,:leader_exit_pending,:realised_net_native,:realised_user_net_native,:profit_fee_native,:exit_tx,:exit_reason,:closed_at,:updated_at)""", row)
        conn.commit()
    _attach_leader(app, row["position_id"], row["primary_leader"], row.get("leader_buy_tx"), primary=True)
    export_positions(app)


def _user_leader_rank(app, telegram_id, chain_id, wallet) -> int | None:
    with closing(connect(app)) as conn:
        r = conn.execute("SELECT rank FROM leaders WHERE telegram_id=? AND chain_id=? AND wallet=?", (str(telegram_id), int(chain_id), str(wallet).lower())).fetchone()
        return int(r["rank"]) if r else None


def _notify(app, tid, text):
    if not app.telegram_bot_token or not tid:
        return
    try: send_message(app.telegram_bot_token, str(tid), text, parse_mode="HTML")
    except Exception: pass


def process_leader_event(app, event: dict) -> list[dict]:
    chain = _chain(app, chain_id=event["chain_id"])
    if not chain:
        return []
    actions = []
    for u in all_users(app.csv_dir, enabled_only=True):
        tid = str(u.get("telegram_id") or "")
        rank = _user_leader_rank(app, tid, chain.chain_id, event["leader_wallet"])
        if rank is None:
            continue
        cfg = user_settings(app, tid, chain.chain_id)
        if event["action"] == "BUY":
            if not _bool(cfg.get("enabled"), False):
                continue
            existing = _open_position_for_token(app, tid, chain.chain_id, event["token"])
            if existing:
                _attach_leader(app, existing["position_id"], event["leader_wallet"], event["tx_hash"], primary=False)
                actions.append({"telegram_id": tid, "action": "CONSENSUS", "position_id": existing["position_id"]})
                continue
            with closing(connect(app)) as conn:
                open_count = conn.execute("SELECT COUNT(*) n FROM positions WHERE telegram_id=? AND chain_id=? AND status='OPEN'", (tid, chain.chain_id)).fetchone()["n"]
            if int(open_count) >= _int(cfg.get("max_positions_per_chain"), 5):
                continue
            try:
                trader = LiveTrader(app, chain.slug, telegram_id=tid)
                amount = _position_size(app, tid, trader, cfg)
                if amount <= 0:
                    continue
                live = _bool(cfg.get("auto_trade_enabled"), False)
                if live:
                    ok, reason = _gate_live(app, tid, chain)
                    if not ok:
                        continue
                ok, reason, check = _validate_entry(app, trader, event, amount, cfg, live)
                if not ok:
                    continue
                q = check["quote"]; now = int(time.time()); entry_tx = ""; actual_raw = int(check["expected_raw"])
                gas_cost = _estimated_gas_native(trader, cfg, "BUY")
                if live:
                    before_raw = trader.token_balance(event["token"])[4]
                    result = trader.buy(event["token"], amount, "CONFIRM")
                    receipt = trader.w3.eth.wait_for_transaction_receipt(result["tx_hash"], timeout=180, poll_latency=2)
                    if int(receipt.status) != 1:
                        raise LiveTradingError("SiBot BUY transaction failed")
                    after_raw = trader.token_balance(event["token"])[4]
                    actual_raw = max(0, int(after_raw) - int(before_raw))
                    if actual_raw <= 0:
                        raise LiveTradingError("SiBot BUY confirmed but no token balance increase was detected")
                    gas_price = int(receipt.get("effectiveGasPrice") or 0); gas_used = int(receipt.get("gasUsed") or 0)
                    gas_cost = Decimal(gas_price * gas_used) / Decimal(10**18)
                    entry_tx = result["tx_hash"]
                pid = hashlib.sha256(f"{tid}|{chain.chain_id}|{event['token'].lower()}|{event['tx_hash']}|{now}".encode()).hexdigest()[:32]
                row = {
                    "position_id": pid, "telegram_id": tid, "wallet_id": trader.wallet_id or "", "wallet_address": trader.address,
                    "chain_id": chain.chain_id, "chain_slug": chain.slug, "primary_leader": event["leader_wallet"].lower(), "leader_rank": rank,
                    "token": Web3.to_checksum_address(event["token"]), "symbol": q.token_symbol, "decimals": q.token_decimals,
                    "mode": "LIVE" if live else "SHADOW", "status": "OPEN", "token_amount_raw": str(actual_raw),
                    "entry_input_native": str(amount), "entry_cost_native": str(amount + gas_cost), "entry_tx": entry_tx, "entry_ts": now,
                    "leader_buy_tx": event["tx_hash"], "leader_entry_native": str(event.get("native_amount") or 0), "leader_entry_token_raw": str(event.get("token_amount_raw") or 0),
                    "current_exit_native": "0", "unrealised_net_native": str(-gas_cost), "unrealised_pct": 0.0, "peak_unrealised_pct": 0.0,
                    "leader_exit_pending": 0, "realised_net_native": "0", "realised_user_net_native": "0", "profit_fee_native": "0",
                    "exit_tx": "", "exit_reason": "", "closed_at": None, "updated_at": now,
                }
                _insert_position(app, row)
                _append_execution(app, {"timestamp_epoch":now,"telegram_id":tid,"wallet_id":trader.wallet_id or "","chain_id":chain.chain_id,"chain_slug":chain.slug,"position_id":pid,"side":"BUY","mode":row["mode"],"leader_wallet":event["leader_wallet"].lower(),"token":row["token"],"symbol":row["symbol"],"native_amount":str(amount),"realised_net_native":"","profit_fee_native":"","realised_user_net_native":"","tx_hash":entry_tx,"status":"SUCCESS" if live else "SHADOW_SUCCESS","reason":"SIMO_LEADER_BUY"})
                actions.append({"telegram_id": tid, "action": "BUY", "mode": row["mode"], "position_id": pid})
                _notify(app, tid, f"🤖 <b>SiBot {'LIVE BUY' if live else 'SHADOW BUY'}</b>\n{chain.name} | SiMo #{rank} <code>{event['leader_wallet'][:8]}…</code>\nToken: <b>{q.token_symbol}</b>\nAllocation: <b>{amount:f} {chain.native_symbol}</b>\nEntry deterioration: <b>{check['deterioration_pct']:.3f}%</b>\nRound trip: <b>{check['roundtrip_loss_pct']:.3f}%</b>" + (f"\nTX: <code>{entry_tx}</code>" if entry_tx else ""))
            except Exception:
                continue
        elif event["action"] == "SELL":
            actions.extend(_handle_leader_sell(app, tid, chain, event, cfg))
    return actions


def _handle_leader_sell(app, tid, chain, event, cfg) -> list[dict]:
    with closing(connect(app)) as conn:
        rows = conn.execute("""SELECT p.* FROM positions p JOIN position_leaders l ON l.position_id=p.position_id
                               WHERE p.telegram_id=? AND p.chain_id=? AND lower(p.token)=? AND p.status='OPEN' AND l.leader_wallet=? AND l.active=1""",
                            (str(tid), chain.chain_id, str(event["token"]).lower(), str(event["leader_wallet"]).lower())).fetchall()
    out = []
    for rr in rows:
        p = dict(rr); full = _float(event.get("sell_pct"), 100.0) >= 99.0
        with _DB_LOCK, closing(connect(app)) as conn:
            if full:
                conn.execute("UPDATE position_leaders SET active=0,updated_at=? WHERE position_id=? AND leader_wallet=?", (int(time.time()), p["position_id"], event["leader_wallet"].lower()))
            conn.commit()
            active = conn.execute("SELECT COUNT(*) n FROM position_leaders WHERE position_id=? AND active=1", (p["position_id"],)).fetchone()["n"]
        is_primary = str(p["primary_leader"]).lower() == str(event["leader_wallet"]).lower()
        if not is_primary and int(active) > 0:
            continue
        if not full and not _bool(cfg.get("mirror_partial_sells"), True):
            continue
        fraction = max(Decimal("0.0001"), min(Decimal(1), _dec(event.get("sell_pct"), 100) / Decimal(100))) if not full else Decimal(1)
        try:
            evaluation = evaluate_position(app, p, fraction=fraction)
        except Exception:
            evaluation = {"net_pct": Decimal(-999), "net_native": Decimal(-999)}
        min_profit = _dec(cfg.get("min_exit_profit_pct"), ".10")
        stop = _dec(cfg.get("stop_loss_pct"), 10)
        if evaluation["net_pct"] >= min_profit or evaluation["net_pct"] <= -stop:
            reason = "PRIMARY_SIMO_PARTIAL_SELL" if not full else "PRIMARY_SIMO_SELL"
            try: close_position(app, p["position_id"], fraction, reason)
            except Exception: pass
        else:
            with _DB_LOCK, closing(connect(app)) as conn:
                conn.execute("UPDATE positions SET leader_exit_pending=1,updated_at=? WHERE position_id=?", (int(time.time()), p["position_id"]))
                conn.commit()
            out.append({"telegram_id": tid, "action": "EXIT_PENDING", "position_id": p["position_id"]})
    return out


def evaluate_position(app, position: dict, fraction=Decimal(1)) -> dict:
    chain = _chain(app, chain_id=position["chain_id"])
    if not chain:
        raise ValueError("unknown chain")
    cfg = user_settings(app, position["telegram_id"], chain.chain_id)
    trader = LiveTrader(app, chain.slug, telegram_id=position["telegram_id"], wallet_id=position.get("wallet_id") or None)
    remaining_raw = _int(position.get("token_amount_raw"), 0)
    sell_raw = max(1, int(Decimal(remaining_raw) * max(Decimal(0), min(Decimal(1), Decimal(str(fraction))))))
    if position["mode"] == "LIVE":
        _, _, dec, _, balance_raw, _ = trader.token_balance(position["token"])
        sell_raw = min(sell_raw, int(balance_raw))
        if sell_raw <= 0:
            raise ValueError("no live token balance")
    dec = _int(position.get("decimals"), 18)
    human = Decimal(sell_raw) / (Decimal(10) ** dec)
    q, _ = trader.quote_sell(position["token"], str(human))
    proceeds = _dec(q.expected_out_human, 0)
    exit_gas = _estimated_gas_native(trader, cfg, "SELL")
    cost = _dec(position.get("entry_cost_native"), 0) * Decimal(sell_raw) / Decimal(max(1, remaining_raw))
    net = proceeds - exit_gas - cost
    pct = net / cost * Decimal(100) if cost > 0 else Decimal(0)
    return {"proceeds_native": proceeds, "exit_gas_native": exit_gas, "cost_native": cost, "net_native": net, "net_pct": pct, "sell_raw": sell_raw, "sell_human": human}


def close_position(app, position_id: str, fraction=Decimal(1), reason="EXIT") -> dict:
    with closing(connect(app)) as conn:
        r = conn.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()
    if not r or r["status"] != "OPEN":
        raise ValueError("SiBot position is not open")
    p = dict(r); fraction = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    ev = evaluate_position(app, p, fraction)
    chain = _chain(app, chain_id=p["chain_id"]); cfg = user_settings(app, p["telegram_id"], p["chain_id"])
    now = int(time.time()); exit_tx = ""; realised = ev["net_native"]
    sold_raw = int(ev["sell_raw"]); old_raw = max(1, _int(p["token_amount_raw"], 0)); cost_fraction = _dec(p["entry_cost_native"]) * Decimal(sold_raw) / Decimal(old_raw)
    if p["mode"] == "LIVE":
        trader = LiveTrader(app, chain.slug, telegram_id=p["telegram_id"], wallet_id=p.get("wallet_id") or None)
        before_native = trader.native_balance(); before_raw = trader.token_balance(p["token"])[4]
        human = Decimal(sold_raw) / (Decimal(10) ** _int(p.get("decimals"), 18))
        result = trader.sell(p["token"], str(human), "CONFIRM")
        receipt = trader.w3.eth.wait_for_transaction_receipt(result["tx_hash"], timeout=180, poll_latency=2)
        if int(receipt.status) != 1:
            raise LiveTradingError("SiBot SELL transaction failed")
        after_native = trader.native_balance(); after_raw = trader.token_balance(p["token"])[4]
        actual_sold = max(0, int(before_raw) - int(after_raw))
        if actual_sold > 0 and actual_sold != sold_raw:
            sold_raw = actual_sold; cost_fraction = _dec(p["entry_cost_native"]) * Decimal(sold_raw) / Decimal(old_raw)
        proceeds_after_exit_gas = after_native - before_native
        realised = proceeds_after_exit_gas - cost_fraction
        exit_tx = result["tx_hash"]
    fee = Decimal(0); user_net = realised; fee_tx = ""; fee_status = "NONE"
    if p["mode"] == "LIVE" and realised > 0:
        fee = profit_share_amount(app.csv_dir, p["telegram_id"], realised)
        user_net = realised - fee
        master = master_wallet(app.csv_dir, p["chain_id"])
        if fee > 0 and master:
            try:
                trader = LiveTrader(app, chain.slug, telegram_id=p["telegram_id"], wallet_id=p.get("wallet_id") or None)
                fr = trader.transfer_native(master, fee, "CONFIRM"); fee_tx = fr["tx_hash"]; fee_status = "BROADCAST"
            except Exception:
                fee_status = "PENDING"
            ledger(app.csv_dir, {"telegram_id":p["telegram_id"],"wallet_id":p.get("wallet_id") or "","chain_id":p["chain_id"],"fee_type":"PROFIT_SHARE","plan_id":(user_fee_plan(app.csv_dir,p["telegram_id"]) or {}).get("plan_id") or "","gross_profit_base":str(realised),"gas_cost_base":"","net_profit_base":str(realised),"fee_amount_base":str(fee),"fee_asset":chain.native_symbol,"master_address":master or "","tx_hash":fee_tx,"status":fee_status,"note":"SiBot realised spot-position profit share"})
    remaining_raw = max(0, old_raw - sold_raw)
    remaining_cost = max(Decimal(0), _dec(p["entry_cost_native"]) - cost_fraction)
    cumulative = _dec(p.get("realised_net_native"), 0) + realised
    cumulative_user = _dec(p.get("realised_user_net_native"), 0) + user_net
    cumulative_fee = _dec(p.get("profit_fee_native"), 0) + fee
    closed = remaining_raw <= max(1, int(old_raw * 0.001)) or fraction >= Decimal("0.999")
    with _DB_LOCK, closing(connect(app)) as conn:
        conn.execute("""UPDATE positions SET token_amount_raw=?,entry_cost_native=?,realised_net_native=?,realised_user_net_native=?,profit_fee_native=?,exit_tx=?,exit_reason=?,closed_at=?,status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
                     (str(0 if closed else remaining_raw), str(0 if closed else remaining_cost), str(cumulative), str(cumulative_user), str(cumulative_fee), exit_tx, reason, now if closed else None, "CLOSED" if closed else "OPEN", 0 if closed else int(p.get("leader_exit_pending") or 0), now, position_id))
        conn.commit()
    _append_execution(app, {"timestamp_epoch":now,"telegram_id":p["telegram_id"],"wallet_id":p.get("wallet_id") or "","chain_id":p["chain_id"],"chain_slug":p["chain_slug"],"position_id":position_id,"side":"SELL","mode":p["mode"],"leader_wallet":p["primary_leader"],"token":p["token"],"symbol":p.get("symbol") or "","native_amount":str(ev["proceeds_native"]),"realised_net_native":str(realised),"profit_fee_native":str(fee),"realised_user_net_native":str(user_net),"tx_hash":exit_tx,"status":"SUCCESS" if p["mode"]=="LIVE" else "SHADOW_SUCCESS","reason":reason})
    export_positions(app)
    _notify(app, p["telegram_id"], f"🤖 <b>SiBot {'LIVE' if p['mode']=='LIVE' else 'SHADOW'} EXIT</b>\n{chain.name} | <b>{p.get('symbol') or p['token'][:10]}</b>\nReason: <b>{reason}</b>\nNet: <b>{realised:+f} {chain.native_symbol}</b>" + (f"\nUser net after fee: <b>{user_net:+f}</b>" if fee else "") + (f"\nTX: <code>{exit_tx}</code>" if exit_tx else ""))
    return {"position_id": position_id, "closed": closed, "realised_net_native": realised, "user_net_native": user_net, "tx_hash": exit_tx}


def _append_execution(app, row: dict):
    path = Path(app.csv_dir) / "auto" / "sibot_trade_execution.csv"
    headers = ["timestamp_epoch","telegram_id","wallet_id","chain_id","chain_slug","position_id","side","mode","leader_wallet","token","symbol","native_amount","realised_net_native","profit_fee_native","realised_user_net_native","tx_hash","status","reason"]
    rows = _rows(path); rows.append(row); rows = rows[-20000:]; _atomic_csv(path, rows, headers)


def export_positions(app):
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY updated_at DESC").fetchall()]
    headers = list(rows[0].keys()) if rows else ["position_id","telegram_id","wallet_id","wallet_address","chain_id","chain_slug","primary_leader","leader_rank","token","symbol","decimals","mode","status","token_amount_raw","entry_input_native","entry_cost_native","entry_tx","entry_ts","leader_buy_tx","leader_entry_native","leader_entry_token_raw","current_exit_native","unrealised_net_native","unrealised_pct","peak_unrealised_pct","leader_exit_pending","realised_net_native","realised_user_net_native","profit_fee_native","exit_tx","exit_reason","closed_at","updated_at"]
    _atomic_csv(Path(app.csv_dir) / "auto" / "simo_positions.csv", rows, headers)


def position_rows(app, telegram_id, open_only=False) -> list[dict]:
    with closing(connect(app)) as conn:
        if open_only:
            rows = conn.execute("SELECT * FROM positions WHERE telegram_id=? AND status='OPEN' ORDER BY chain_id,entry_ts DESC", (str(telegram_id),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM positions WHERE telegram_id=? ORDER BY updated_at DESC LIMIT 200", (str(telegram_id),)).fetchall()
        out = [dict(r) for r in rows]
        for p in out:
            ls = conn.execute("SELECT * FROM position_leaders WHERE position_id=? ORDER BY primary_flag DESC,joined_at", (p["position_id"],)).fetchall()
            p["leaders"] = [dict(x) for x in ls]
        return out


def monitor_positions(app) -> None:
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY updated_at").fetchall()]
    now = int(time.time())
    for p in rows:
        cfg = user_settings(app, p["telegram_id"], p["chain_id"])
        try:
            ev = evaluate_position(app, p)
        except Exception:
            continue
        peak = max(_float(p.get("peak_unrealised_pct"), 0), float(ev["net_pct"]))
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute("UPDATE positions SET current_exit_native=?,unrealised_net_native=?,unrealised_pct=?,peak_unrealised_pct=?,updated_at=? WHERE position_id=?",
                         (str(ev["proceeds_native"]), str(ev["net_native"]), float(ev["net_pct"]), peak, now, p["position_id"]))
            conn.commit()
        stop = _dec(cfg.get("stop_loss_pct"), 10); take = _dec(cfg.get("take_profit_pct"), 25); min_exit = _dec(cfg.get("min_exit_profit_pct"), .10)
        age_hours = Decimal(max(0, now - _int(p.get("entry_ts"), now))) / Decimal(3600)
        max_hold = _dec(cfg.get("max_hold_hours"), 24)
        reason = None
        if _bool(p.get("leader_exit_pending"), False) and ev["net_pct"] >= min_exit:
            reason = "SIMO_EXIT_NOW_PROFITABLE"
        elif ev["net_pct"] <= -stop:
            reason = "STOP_LOSS"
        elif ev["net_pct"] >= take:
            reason = "TAKE_PROFIT"
        elif age_hours >= max_hold and ev["net_pct"] > 0:
            reason = "MAX_HOLD_PROFIT"
        if reason:
            try: close_position(app, p["position_id"], Decimal(1), reason)
            except Exception: pass
    export_positions(app)


def performance(app, telegram_id) -> dict:
    chains = {c.chain_id: c for c in load_chains(app, enabled_only=False)}
    by_chain = defaultdict(lambda: {"realised": Decimal(0), "fees": Decimal(0), "unrealised": Decimal(0), "live_trades": 0, "shadow_trades": 0})
    with closing(connect(app)) as conn:
        rows = conn.execute("SELECT * FROM positions WHERE telegram_id=?", (str(telegram_id),)).fetchall()
    for r in rows:
        a = by_chain[int(r["chain_id"])]
        if str(r["mode"]).upper() == "LIVE":
            a["realised"] += _dec(r["realised_user_net_native"], 0); a["fees"] += _dec(r["profit_fee_native"], 0)
            if r["status"] == "CLOSED": a["live_trades"] += 1
            if r["status"] == "OPEN": a["unrealised"] += _dec(r["unrealised_net_native"], 0)
        else:
            if r["status"] == "CLOSED": a["shadow_trades"] += 1
    return {"by_chain": {cid: {**v, "chain_slug": chains[cid].slug if cid in chains else str(cid), "native_symbol": chains[cid].native_symbol if cid in chains else "NATIVE"} for cid, v in by_chain.items()}}


def _history_worker(app):
    while True:
        try:
            ensure_settings(app)
            if _bool(platform_settings(app, 0).get("platform_enabled"), True):
                for c in load_chains(app, enabled_only=True):
                    # Isolate each chain: an exception fetching one chain's history
                    # must not abort the loop and starve every chain after it in
                    # iteration order for the rest of this pass.
                    try:
                        wallet = _next_history_wallet(app, c)
                        if wallet:
                            refresh_wallet_history(app, c, wallet)
                    except Exception as exc:
                        print(f"[sibot-history:{c.slug}]", type(exc).__name__, exc)
                refresh_all_rankings(app)
        except Exception as exc:
            print("[sibot-history]", type(exc).__name__, exc)
        time.sleep(max(5, _int(platform_settings(app, 0).get("history_worker_seconds"), 12)))


def _live_worker(app):
    last_positions = 0; last_rank = 0
    while True:
        now = int(time.time())
        try:
            if _bool(platform_settings(app, 0).get("platform_enabled"), True):
                if now - last_rank >= max(30, _int(platform_settings(app, 0).get("ranking_refresh_seconds"), 120)):
                    refresh_all_rankings(app); last_rank = now
                for c in load_chains(app, enabled_only=True):
                    try: poll_leader_blocks(app, c)
                    except Exception as exc: print(f"[sibot-monitor:{c.slug}]", type(exc).__name__, exc)
                if now - last_positions >= max(3, _int(platform_settings(app, 0).get("position_interval_seconds"), 10)):
                    monitor_positions(app); last_positions = now
        except Exception as exc:
            print("[sibot-live]", type(exc).__name__, exc)
        time.sleep(max(1, _int(platform_settings(app, 0).get("monitor_interval_seconds"), 3)))


def start_workers(app):
    global _WORKERS_STARTED
    with _WORKER_LOCK:
        if _WORKERS_STARTED:
            return
        _WORKERS_STARTED = True
    ensure_settings(app); connect(app).close()
    threading.Thread(target=_history_worker, args=(app,), daemon=True, name="sibot-history").start()
    threading.Thread(target=_live_worker, args=(app,), daemon=True, name="sibot-live").start()
    print("[sibot] SiBot/SiMo research, leader monitor and position monitor started")


def set_user_value(app, telegram_id, key: str, value, chain_id="*"):
    if key not in DEFAULTS and key not in {"enabled", "auto_trade_enabled"}:
        raise ValueError("Unknown SiBot setting")
    from .user_registry import set_user_setting
    setting = "sibot_enabled" if key == "enabled" else "sibot_auto_trade_enabled" if key == "auto_trade_enabled" else f"sibot_{key}"
    set_user_setting(app.csv_dir, telegram_id, setting, str(value), chain_id=chain_id, description=f"SiBot {key}")
    if key in {"lookback_days", "leaders_per_chain", "min_closed_trades", "min_win_rate_pct"}:
        refresh_all_rankings(app, telegram_id)


def setting_value(app, telegram_id, key, chain_id=0):
    return user_settings(app, telegram_id, chain_id).get(key)
