from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from . import sibot as _sibot
from .user_registry import all_users

SOLANA_SLUG = "solana"
SOLANA_NAME = "Solana"
SOLANA_CHAIN_ID = -101
SOLANA_NATIVE = "SOL"
WSOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_EXPLORER = "https://solscan.io"
DEFAULT_JUPITER_ORDER = "https://api.jup.ag/swap/v2/order"

DEFAULTS = {
    "enabled": ("true", "Enable Solana SiBot research and SHADOW monitoring"),
    "rpc_url": (DEFAULT_RPC, "Solana JSON-RPC endpoint; use a dedicated provider for sustained production load"),
    "explorer_url": (DEFAULT_EXPLORER, "Solana account/transaction explorer"),
    "lookback_days": ("60", "Realised SOL-denominated Top-20 lookback"),
    "discovery_blocks_per_cycle": ("2", "Finalized blocks inspected per discovery cycle"),
    "discovery_interval_seconds": ("15", "Pause between bounded Solana discovery passes"),
    "candidate_limit": ("100", "Active Solana swap wallets retained for history backfill"),
    "history_max_signatures": ("250", "Maximum recent signatures reconstructed per candidate refresh"),
    "history_refresh_hours": ("12", "Refresh a Solana candidate after this many hours"),
    "rpc_delay_seconds": ("0.20", "Delay between heavy Solana history RPC calls"),
    "leaders_per_user": ("2", "Solana leaders selected per SiBot user"),
    "min_closed_trades": ("5", "Minimum reconstructed SOL round trips for copy-leader eligibility"),
    "min_win_rate_pct": ("50", "Minimum positive reconstructed Solana results for leader eligibility"),
    "leader_poll_seconds": ("5", "Fresh Solana leader signature polling cadence"),
    "position_poll_seconds": ("15", "Solana SHADOW open-position quote cadence"),
    "max_signal_age_seconds": ("30", "Maximum age of a Solana leader BUY before SHADOW entry"),
    "shadow_allocation_sol": ("0.05", "Nominal SOL amount used for each Solana SHADOW copied entry"),
    "max_roundtrip_loss_pct": ("3", "Maximum immediate Jupiter quote round-trip loss for a SHADOW entry"),
    "max_entry_deterioration_pct": ("2", "Maximum worse current entry versus the observed leader entry"),
    "estimated_entry_fee_sol": ("0.00002", "Conservative estimated Solana entry network/priority fee for SHADOW PnL"),
    "estimated_exit_fee_sol": ("0.00002", "Conservative estimated Solana exit network/priority fee for SHADOW PnL"),
    "stop_loss_pct": ("10", "Independent Solana SHADOW stop loss"),
    "take_profit_pct": ("25", "Independent Solana SHADOW take profit"),
    "leader_exit_loss_cap_pct": ("2.5", "Maximum tolerated loss after the Solana leader has exited"),
    "break_even_trigger_pct": ("5", "Solana SHADOW break-even protection activation"),
    "break_even_floor_pct": ("0.10", "Solana SHADOW net floor after break-even activation"),
    "trailing_trigger_pct": ("10", "Solana SHADOW trailing-profit activation"),
    "trailing_gap_pct": ("5", "Solana SHADOW maximum give-back from peak"),
    "max_hold_hours": ("24", "Close old profitable Solana SHADOW positions"),
    "mirror_partial_sells": ("true", "Mirror the Solana leader's observed partial sell fraction"),
    "jupiter_order_url": (DEFAULT_JUPITER_ORDER, "Jupiter Swap V2 order endpoint used for quote-only SHADOW valuation"),
}

_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_DB_LOCK = threading.RLock()
_JUPITER_LOCK = threading.Lock()
_LAST_JUPITER_CALL = 0.0
_REFRESH_NOW = set()


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)


def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def settings_path(app) -> Path:
    return Path(app.csv_dir) / "solana_settings.csv"


def ensure_settings(app) -> Path:
    path = settings_path(app)
    headers = ["setting", "value", "description"]
    rows = _sibot._rows(path)
    known = {str(r.get("setting") or "").strip() for r in rows}
    changed = False
    for key, (value, description) in DEFAULTS.items():
        if key not in known:
            rows.append({"setting": key, "value": value, "description": description})
            changed = True
    if changed or not path.exists():
        _sibot._atomic_csv(path, rows, headers)
    return path


def settings(app) -> dict:
    ensure_settings(app)
    out = {}
    for r in _sibot._rows(settings_path(app)):
        key = str(r.get("setting") or "").strip()
        if key:
            out[key] = str(r.get("value") or "").strip()
    if os.getenv("SOLANA_RPC_URL", "").strip():
        out["rpc_url"] = os.getenv("SOLANA_RPC_URL", "").strip()
    if os.getenv("SOLANA_EXPLORER_URL", "").strip():
        out["explorer_url"] = os.getenv("SOLANA_EXPLORER_URL", "").strip()
    return out


def db_path(app) -> Path:
    return Path(app.data_dir) / "solana_sibot.sqlite3"


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS candidates(
  wallet TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  swap_events INTEGER NOT NULL DEFAULT 0,
  last_signature TEXT,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_candidates ON candidates(swap_events DESC,last_seen DESC);

CREATE TABLE IF NOT EXISTS history_status(
  wallet TEXT PRIMARY KEY,
  fetched_at INTEGER NOT NULL,
  coverage_start_ts INTEGER,
  coverage_end_ts INTEGER,
  signatures INTEGER NOT NULL DEFAULT 0,
  swaps INTEGER NOT NULL DEFAULT 0,
  closed_trades INTEGER NOT NULL DEFAULT 0,
  truncated INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS trades(
  trade_id TEXT PRIMARY KEY,
  wallet TEXT NOT NULL,
  mint TEXT NOT NULL,
  decimals INTEGER NOT NULL,
  buy_signature TEXT NOT NULL,
  sell_signature TEXT NOT NULL,
  buy_ts INTEGER NOT NULL,
  sell_ts INTEGER NOT NULL,
  token_amount_raw TEXT NOT NULL,
  cost_sol TEXT NOT NULL,
  proceeds_sol TEXT NOT NULL,
  net_sol TEXT NOT NULL,
  hold_seconds INTEGER NOT NULL,
  source TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_trades_rank ON trades(wallet,sell_ts);

CREATE TABLE IF NOT EXISTS rankings(
  telegram_id TEXT NOT NULL,
  lookback_days INTEGER NOT NULL,
  rank INTEGER NOT NULL,
  wallet TEXT NOT NULL,
  gross_profit_sol TEXT NOT NULL,
  gross_loss_sol TEXT NOT NULL,
  net_profit_sol TEXT NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  closed_trades INTEGER NOT NULL,
  win_rate REAL NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,wallet)
);
CREATE INDEX IF NOT EXISTS idx_sol_rankings ON rankings(telegram_id,rank);

CREATE TABLE IF NOT EXISTS leaders(
  telegram_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  wallet TEXT NOT NULL,
  net_profit_sol TEXT NOT NULL,
  win_rate REAL NOT NULL,
  closed_trades INTEGER NOT NULL,
  selected_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,wallet)
);
CREATE INDEX IF NOT EXISTS idx_sol_leaders ON leaders(wallet);

CREATE TABLE IF NOT EXISTS leader_events(
  event_id TEXT PRIMARY KEY,
  leader_wallet TEXT NOT NULL,
  signature TEXT NOT NULL,
  action TEXT NOT NULL,
  mint TEXT NOT NULL,
  decimals INTEGER NOT NULL,
  token_amount_raw TEXT NOT NULL,
  sol_amount TEXT NOT NULL,
  sell_pct REAL,
  slot INTEGER NOT NULL,
  event_ts INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_events ON leader_events(leader_wallet,event_ts);

CREATE TABLE IF NOT EXISTS positions(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  leader_wallet TEXT NOT NULL,
  leader_rank INTEGER,
  mint TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  token_amount_raw TEXT NOT NULL,
  entry_cost_sol TEXT NOT NULL,
  entry_ts INTEGER NOT NULL,
  leader_buy_signature TEXT,
  leader_entry_sol TEXT,
  leader_entry_token_raw TEXT,
  signal_count INTEGER NOT NULL DEFAULT 1,
  current_exit_sol TEXT NOT NULL DEFAULT '0',
  unrealised_net_sol TEXT NOT NULL DEFAULT '0',
  unrealised_pct REAL NOT NULL DEFAULT 0,
  peak_unrealised_pct REAL NOT NULL DEFAULT 0,
  leader_exit_pending INTEGER NOT NULL DEFAULT 0,
  realised_net_sol TEXT NOT NULL DEFAULT '0',
  exit_signature TEXT,
  exit_reason TEXT,
  closed_at INTEGER,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_positions ON positions(telegram_id,status,mint);

CREATE TABLE IF NOT EXISTS state(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def connect(app):
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


def _rpc(app, method: str, params: list):
    cfg = settings(app)
    url = cfg.get("rpc_url") or DEFAULT_RPC
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(url, json=payload, timeout=35, headers={"User-Agent": "BOOT-SiBot-Solana/1.0"})
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Solana RPC {method}: {str(data['error'])[:400]}")
    return data.get("result")


def _account_keys(result: dict) -> list[str]:
    try:
        keys = result["transaction"]["message"]["accountKeys"]
    except Exception:
        return []
    out = []
    for x in keys:
        if isinstance(x, dict):
            out.append(str(x.get("pubkey") or ""))
        else:
            out.append(str(x))
    return out


def _signers(result: dict) -> list[str]:
    try:
        keys = result["transaction"]["message"]["accountKeys"]
    except Exception:
        return []
    if keys and isinstance(keys[0], dict):
        return [str(x.get("pubkey") or "") for x in keys if x.get("signer")]
    try:
        n = int(result["transaction"]["message"]["header"]["numRequiredSignatures"])
    except Exception:
        n = 1
    return [str(x) for x in keys[:n]]


def _token_state(result: dict, wallet: str):
    meta = result.get("meta") or {}
    wallet = str(wallet)
    pre = defaultdict(int)
    post = defaultdict(int)
    decimals = {}
    for field, target in (("preTokenBalances", pre), ("postTokenBalances", post)):
        for row in meta.get(field) or []:
            if str(row.get("owner") or "") != wallet:
                continue
            mint = str(row.get("mint") or "")
            ui = row.get("uiTokenAmount") or {}
            try:
                target[mint] += int(ui.get("amount") or 0)
                decimals[mint] = int(ui.get("decimals") or 0)
            except Exception:
                continue
    deltas = {}
    for mint in set(pre) | set(post):
        d = int(post.get(mint, 0)) - int(pre.get(mint, 0))
        if d:
            deltas[mint] = d
    return deltas, dict(pre), dict(post), decimals


def _sol_delta(result: dict, wallet: str) -> Decimal:
    keys = _account_keys(result)
    try:
        idx = keys.index(str(wallet))
    except ValueError:
        return Decimal(0)
    meta = result.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return Decimal(0)
    return Decimal(int(post[idx]) - int(pre[idx])) / Decimal(1_000_000_000)


def _looks_like_swap(result: dict) -> bool:
    logs = ((result.get("meta") or {}).get("logMessages") or [])
    text = "\n".join(str(x).lower() for x in logs)
    return "swap" in text or "instruction: buy" in text or "instruction: sell" in text


def classify_swap(result: dict, wallet: str) -> dict | None:
    meta = result.get("meta") or {}
    if meta.get("err") is not None or not _looks_like_swap(result):
        return None
    deltas, pre, post, decimals = _token_state(result, wallet)
    deltas.pop(WSOL_MINT, None)
    positive = [(m, d) for m, d in deltas.items() if d > 0]
    negative = [(m, -d) for m, d in deltas.items() if d < 0]
    sol = _sol_delta(result, wallet)
    if sol < Decimal("-0.000005") and len(positive) == 1 and not negative:
        mint, raw = positive[0]
        action = "BUY"
        sol_amount = -sol
        sell_pct = None
    elif sol > Decimal("0.000005") and len(negative) == 1 and not positive:
        mint, raw = negative[0]
        action = "SELL"
        sol_amount = sol
        before = int(pre.get(mint, 0))
        sell_pct = float(Decimal(raw) * Decimal(100) / Decimal(max(1, before)))
    else:
        return None
    signature = ""
    try:
        signature = str((result.get("transaction") or {}).get("signatures", [""])[0])
    except Exception:
        pass
    return {
        "action": action,
        "wallet": str(wallet),
        "mint": mint,
        "decimals": int(decimals.get(mint, 0)),
        "token_amount_raw": int(raw),
        "sol_amount": sol_amount,
        "sell_pct": sell_pct,
        "signature": signature,
        "slot": int(result.get("slot") or 0),
        "event_ts": int(result.get("blockTime") or time.time()),
    }


def _get_transaction(app, signature: str):
    return _rpc(app, "getTransaction", [
        signature,
        {"commitment": "finalized", "maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"},
    ])


def _get_signatures(app, wallet: str, limit=20, before=None):
    opts = {"commitment": "finalized", "limit": max(1, min(1000, int(limit)))}
    if before:
        opts["before"] = before
    return _rpc(app, "getSignaturesForAddress", [wallet, opts]) or []


def discover_recent_blocks(app) -> int:
    cfg = settings(app)
    if not _bool(cfg.get("enabled"), True):
        return 0
    latest = int(_rpc(app, "getSlot", [{"commitment": "finalized"}]) or 0)
    blocks_per = max(1, min(10, _int(cfg.get("discovery_blocks_per_cycle"), 2)))
    with closing(connect(app)) as conn:
        last = _int(_state(conn, "last_discovery_slot", 0), 0)
        if last <= 0:
            last = max(0, latest - 6)
        if latest - last > 100:
            last = max(0, latest - 50)
        start = last + 1
        end = min(latest, start + blocks_per - 1)
    found = 0
    for slot in range(start, end + 1):
        try:
            block = _rpc(app, "getBlock", [slot, {
                "commitment": "finalized", "encoding": "jsonParsed", "transactionDetails": "full",
                "rewards": False, "maxSupportedTransactionVersion": 0,
            }])
        except Exception:
            block = None
        if not block:
            continue
        block_time = int(block.get("blockTime") or time.time())
        for item in block.get("transactions") or []:
            result = {"slot": slot, "blockTime": block_time, "transaction": item.get("transaction") or {}, "meta": item.get("meta") or {}}
            if result["meta"].get("err") is not None or not _looks_like_swap(result):
                continue
            for wallet in _signers(result)[:2]:
                event = classify_swap(result, wallet)
                if not event:
                    continue
                now = int(time.time())
                with _DB_LOCK, closing(connect(app)) as conn:
                    conn.execute(
                        """INSERT INTO candidates(wallet,first_seen,last_seen,swap_events,last_signature,updated_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(wallet) DO UPDATE SET last_seen=excluded.last_seen,
                             swap_events=candidates.swap_events+1,last_signature=excluded.last_signature,updated_at=excluded.updated_at""",
                        (wallet, event["event_ts"], event["event_ts"], 1, event["signature"], now),
                    )
                    conn.commit()
                found += 1
    if end >= start:
        with closing(connect(app)) as conn:
            _set_state(conn, "last_discovery_slot", end)
    return found


def _fetch_history_signatures(app, wallet: str, cutoff: int, maximum: int):
    rows = []
    before = None
    truncated = False
    while len(rows) < maximum:
        batch = _get_signatures(app, wallet, min(1000, maximum - len(rows)), before=before)
        if not batch:
            break
        rows.extend(batch)
        oldest = batch[-1]
        ts = _int(oldest.get("blockTime"), 0)
        if ts and ts <= cutoff:
            break
        if len(batch) < min(1000, maximum - (len(rows) - len(batch))):
            break
        before = str(oldest.get("signature") or "")
        if not before:
            break
    if len(rows) >= maximum:
        oldest_ts = min((_int(r.get("blockTime"), 0) for r in rows if _int(r.get("blockTime"), 0)), default=0)
        truncated = bool(oldest_ts and oldest_ts > cutoff)
    return rows[:maximum], truncated


def _match_events(wallet: str, events: list[dict]):
    lots = defaultdict(deque)
    trades = []
    seq = 0
    for ev in sorted(events, key=lambda x: (x["event_ts"], x["signature"])):
        mint = ev["mint"]
        if ev["action"] == "BUY":
            lots[mint].append({**ev, "remaining": int(ev["token_amount_raw"]), "remaining_cost": _dec(ev["sol_amount"])})
            continue
        remaining = int(ev["token_amount_raw"])
        original = max(1, remaining)
        while remaining > 0 and lots[mint]:
            lot = lots[mint][0]
            qty = min(remaining, int(lot["remaining"]))
            buy_fraction = Decimal(qty) / Decimal(max(1, int(lot["remaining"])))
            sell_fraction = Decimal(qty) / Decimal(original)
            cost = _dec(lot["remaining_cost"]) * buy_fraction
            proceeds = _dec(ev["sol_amount"]) * sell_fraction
            net = proceeds - cost
            seq += 1
            tid = hashlib.sha256(f"solana|{wallet}|{lot['signature']}|{ev['signature']}|{mint}|{seq}".encode()).hexdigest()[:32]
            trades.append({
                "trade_id": tid, "wallet": wallet, "mint": mint, "decimals": int(ev.get("decimals") or lot.get("decimals") or 0),
                "buy_signature": lot["signature"], "sell_signature": ev["signature"],
                "buy_ts": int(lot["event_ts"]), "sell_ts": int(ev["event_ts"]), "token_amount_raw": str(qty),
                "cost_sol": str(cost), "proceeds_sol": str(proceeds), "net_sol": str(net),
                "hold_seconds": max(0, int(ev["event_ts"]) - int(lot["event_ts"])),
                "source": "SOLANA_FINALIZED_SOL_DELTA_FIFO", "updated_at": int(time.time()),
            })
            lot["remaining"] -= qty
            lot["remaining_cost"] = _dec(lot["remaining_cost"]) - cost
            remaining -= qty
            if lot["remaining"] <= 0:
                lots[mint].popleft()
    return trades


def refresh_wallet_history(app, wallet: str) -> dict:
    cfg = settings(app)
    lookback = max(1, min(365, _int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    maximum = max(20, min(2000, _int(cfg.get("history_max_signatures"), 250)))
    delay = max(0.05, min(2.0, _float(cfg.get("rpc_delay_seconds"), .20)))
    now = int(time.time())
    try:
        signatures, truncated = _fetch_history_signatures(app, wallet, cutoff, maximum)
        events = []
        for row in reversed(signatures):
            ts = _int(row.get("blockTime"), 0)
            if ts and ts < cutoff:
                continue
            sig = str(row.get("signature") or "")
            if not sig or row.get("err") is not None:
                continue
            try:
                tx = _get_transaction(app, sig)
                if tx:
                    ev = classify_swap(tx, wallet)
                    if ev:
                        events.append(ev)
            except Exception:
                pass
            time.sleep(delay)
        trades = _match_events(wallet, events)
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute("DELETE FROM trades WHERE wallet=?", (wallet,))
            for r in trades:
                conn.execute(
                    """INSERT INTO trades(trade_id,wallet,mint,decimals,buy_signature,sell_signature,buy_ts,sell_ts,
                                           token_amount_raw,cost_sol,proceeds_sol,net_sol,hold_seconds,source,updated_at)
                       VALUES(:trade_id,:wallet,:mint,:decimals,:buy_signature,:sell_signature,:buy_ts,:sell_ts,
                              :token_amount_raw,:cost_sol,:proceeds_sol,:net_sol,:hold_seconds,:source,:updated_at)""", r)
            times = [int(e["event_ts"]) for e in events]
            conn.execute(
                """INSERT INTO history_status(wallet,fetched_at,coverage_start_ts,coverage_end_ts,signatures,swaps,closed_trades,truncated,error)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(wallet) DO UPDATE SET fetched_at=excluded.fetched_at,coverage_start_ts=excluded.coverage_start_ts,
                     coverage_end_ts=excluded.coverage_end_ts,signatures=excluded.signatures,swaps=excluded.swaps,
                     closed_trades=excluded.closed_trades,truncated=excluded.truncated,error=excluded.error""",
                (wallet, now, min(times) if times else now, max(times) if times else now, len(signatures), len(events), len(trades), 1 if truncated else 0, ""),
            )
            conn.commit()
        return {"wallet": wallet, "signatures": len(signatures), "swaps": len(events), "closed_trades": len(trades), "truncated": truncated}
    except Exception as exc:
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute(
                """INSERT INTO history_status(wallet,fetched_at,error) VALUES(?,?,?)
                   ON CONFLICT(wallet) DO UPDATE SET fetched_at=excluded.fetched_at,error=excluded.error""",
                (wallet, now, f"{type(exc).__name__}: {str(exc)[:500]}"),
            )
            conn.commit()
        return {"wallet": wallet, "error": str(exc)}


def _next_history_wallet(app):
    cfg = settings(app)
    limit = max(20, min(500, _int(cfg.get("candidate_limit"), 100)))
    refresh_after = max(1, _int(cfg.get("history_refresh_hours"), 12)) * 3600
    now = int(time.time())
    with closing(connect(app)) as conn:
        candidates = [str(r["wallet"]) for r in conn.execute(
            "SELECT wallet FROM candidates ORDER BY swap_events DESC,last_seen DESC LIMIT ?", (limit,)
        ).fetchall()]
        fetched = {str(r["wallet"]): int(r["fetched_at"] or 0) for r in conn.execute("SELECT wallet,fetched_at FROM history_status").fetchall()}
        leaders = [str(r["wallet"]) for r in conn.execute("SELECT DISTINCT wallet FROM leaders").fetchall()]
    ordered = []
    for w in leaders + candidates:
        if w not in ordered:
            ordered.append(w)
    ordered.sort(key=lambda w: (0 if w in _REFRESH_NOW else 1, fetched.get(w, 0)))
    for wallet in ordered:
        if wallet in _REFRESH_NOW or now - fetched.get(wallet, 0) >= refresh_after:
            _REFRESH_NOW.discard(wallet)
            return wallet
    return None


def refresh_rankings(app, telegram_id=None):
    cfg = settings(app)
    lookback = max(1, min(365, _int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    min_closed = max(1, _int(cfg.get("min_closed_trades"), 5))
    min_win = max(0.0, min(100.0, _float(cfg.get("min_win_rate_pct"), 50)))
    leaders_n = max(1, min(10, _int(cfg.get("leaders_per_user"), 2)))
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if telegram_id is not None:
        users = [u for u in users if str(u.get("telegram_id")) == str(telegram_id)]
    with closing(connect(app)) as conn:
        rows = conn.execute("SELECT wallet,net_sol FROM trades WHERE sell_ts>=?", (cutoff,)).fetchall()
    agg = defaultdict(lambda: {"profit": Decimal(0), "loss": Decimal(0), "net": Decimal(0), "wins": 0, "losses": 0, "closed": 0})
    for r in rows:
        a = agg[str(r["wallet"])]
        n = _dec(r["net_sol"])
        a["net"] += n
        a["closed"] += 1
        if n > 0:
            a["profit"] += n; a["wins"] += 1
        elif n < 0:
            a["loss"] += -n; a["losses"] += 1
    ranked = []
    for wallet, a in agg.items():
        if not (a["net"] > 0 and a["profit"] > a["loss"]):
            continue
        a = dict(a)
        a["wallet"] = wallet
        a["win_rate"] = a["wins"] / a["closed"] * 100.0 if a["closed"] else 0.0
        ranked.append(a)
    ranked.sort(key=lambda x: (x["net"], x["profit"], x["closed"], x["win_rate"]), reverse=True)
    top = ranked[:20]
    now = int(time.time())
    with _DB_LOCK, closing(connect(app)) as conn:
        for u in users:
            tid = str(u.get("telegram_id") or "")
            if not tid:
                continue
            conn.execute("DELETE FROM rankings WHERE telegram_id=?", (tid,))
            for i, a in enumerate(top, 1):
                conn.execute(
                    """INSERT INTO rankings(telegram_id,lookback_days,rank,wallet,gross_profit_sol,gross_loss_sol,net_profit_sol,
                                              wins,losses,closed_trades,win_rate,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, lookback, i, a["wallet"], str(a["profit"]), str(a["loss"]), str(a["net"]), a["wins"], a["losses"], a["closed"], a["win_rate"], now),
                )
            old = {str(r["wallet"]): int(r["selected_at"] or now) for r in conn.execute("SELECT wallet,selected_at FROM leaders WHERE telegram_id=?", (tid,)).fetchall()}
            conn.execute("DELETE FROM leaders WHERE telegram_id=?", (tid,))
            safe = [a for a in top if a["closed"] >= min_closed and a["win_rate"] >= min_win]
            for i, a in enumerate(safe[:leaders_n], 1):
                conn.execute(
                    "INSERT INTO leaders(telegram_id,rank,wallet,net_profit_sol,win_rate,closed_trades,selected_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (tid, i, a["wallet"], str(a["net"]), a["win_rate"], a["closed"], old.get(a["wallet"], now), now),
                )
            conn.commit()
    export_csv(app)
    return top


def ranking_rows(app, telegram_id):
    with closing(connect(app)) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM rankings WHERE telegram_id=? ORDER BY rank", (str(telegram_id),)).fetchall()]


def leader_rows(app, telegram_id):
    with closing(connect(app)) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM leaders WHERE telegram_id=? ORDER BY rank", (str(telegram_id),)).fetchall()]


def position_rows(app, telegram_id, open_only=False):
    with closing(connect(app)) as conn:
        if open_only:
            rows = conn.execute("SELECT * FROM positions WHERE telegram_id=? AND status='OPEN' ORDER BY entry_ts DESC", (str(telegram_id),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM positions WHERE telegram_id=? ORDER BY updated_at DESC LIMIT 100", (str(telegram_id),)).fetchall()
        return [dict(r) for r in rows]


def status(app):
    with closing(connect(app)) as conn:
        c = conn.execute("SELECT COUNT(*) n FROM candidates").fetchone()["n"]
        h = conn.execute("SELECT COUNT(*) n FROM history_status WHERE closed_trades>0").fetchone()["n"]
        t = conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
        l = conn.execute("SELECT COUNT(DISTINCT wallet) n FROM leaders").fetchone()["n"]
        p = conn.execute("SELECT COUNT(*) n FROM positions WHERE status='OPEN'").fetchone()["n"]
    return {"candidates": int(c), "histories": int(h), "closed_trades": int(t), "leaders": int(l), "open_positions": int(p)}


def request_refresh(app):
    with closing(connect(app)) as conn:
        for r in conn.execute("SELECT wallet FROM candidates ORDER BY swap_events DESC,last_seen DESC LIMIT 100").fetchall():
            _REFRESH_NOW.add(str(r["wallet"]))


def _jupiter_throttle(api_key: str):
    global _LAST_JUPITER_CALL
    with _JUPITER_LOCK:
        gap = 0.30 if api_key else 2.10
        wait = gap - (time.time() - _LAST_JUPITER_CALL)
        if wait > 0:
            time.sleep(wait)
        _LAST_JUPITER_CALL = time.time()


def jupiter_quote(app, input_mint: str, output_mint: str, amount_raw: int):
    if int(amount_raw) <= 0:
        raise ValueError("Jupiter quote amount must be positive")
    cfg = settings(app)
    url = cfg.get("jupiter_order_url") or DEFAULT_JUPITER_ORDER
    api_key = os.getenv("JUPITER_API_KEY", "").strip()
    _jupiter_throttle(api_key)
    headers = {"User-Agent": "BOOT-SiBot-Solana/1.0"}
    if api_key:
        headers["x-api-key"] = api_key
    r = requests.get(url, params={"inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount_raw))}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Jupiter quote: {str(data.get('error'))[:300]}")
    out = _int(data.get("outAmount"), 0)
    if out <= 0:
        raise RuntimeError("Jupiter quote returned no output amount")
    return data


def _leader_rank(app, tid, wallet):
    with closing(connect(app)) as conn:
        r = conn.execute("SELECT rank FROM leaders WHERE telegram_id=? AND wallet=?", (str(tid), str(wallet))).fetchone()
        return int(r["rank"]) if r else None


def _open_position(app, tid, mint):
    with closing(connect(app)) as conn:
        r = conn.execute("SELECT * FROM positions WHERE telegram_id=? AND mint=? AND status='OPEN' ORDER BY entry_ts LIMIT 1", (str(tid), str(mint))).fetchone()
        return dict(r) if r else None


def _validate_shadow_entry(app, event: dict, allocation_sol: Decimal, cfg: dict):
    age = max(0, int(time.time()) - int(event["event_ts"]))
    if age > _int(cfg.get("max_signal_age_seconds"), 30):
        return False, f"stale signal {age}s", {}
    lamports = int(allocation_sol * Decimal(1_000_000_000))
    q = jupiter_quote(app, WSOL_MINT, event["mint"], lamports)
    out_raw = _int(q.get("outAmount"), 0)
    back = jupiter_quote(app, event["mint"], WSOL_MINT, out_raw)
    back_sol = Decimal(_int(back.get("outAmount"), 0)) / Decimal(1_000_000_000)
    roundtrip = max(Decimal(0), (Decimal(1) - back_sol / allocation_sol) * Decimal(100)) if allocation_sol > 0 else Decimal(100)
    if roundtrip > _dec(cfg.get("max_roundtrip_loss_pct"), 3):
        return False, f"round-trip loss {roundtrip:.3f}%", {"roundtrip_loss_pct": roundtrip}
    leader_sol = _dec(event.get("sol_amount"), 0)
    leader_raw = Decimal(int(event.get("token_amount_raw") or 0))
    deterioration = Decimal(0)
    if leader_sol > 0 and leader_raw > 0 and out_raw > 0:
        leader_raw_per_sol = leader_raw / leader_sol
        ours_raw_per_sol = Decimal(out_raw) / allocation_sol
        deterioration = max(Decimal(0), (leader_raw_per_sol / ours_raw_per_sol - Decimal(1)) * Decimal(100))
        if deterioration > _dec(cfg.get("max_entry_deterioration_pct"), 2):
            return False, f"entry deterioration {deterioration:.3f}%", {"deterioration_pct": deterioration, "roundtrip_loss_pct": roundtrip}
    return True, "PASS", {"out_raw": out_raw, "deterioration_pct": deterioration, "roundtrip_loss_pct": roundtrip}


def evaluate_position(app, position: dict, fraction=Decimal(1)):
    cfg = settings(app)
    remaining = max(1, _int(position.get("token_amount_raw"), 0))
    f = max(Decimal("0.0001"), min(Decimal(1), Decimal(str(fraction))))
    raw = max(1, int(Decimal(remaining) * f))
    quote = jupiter_quote(app, position["mint"], WSOL_MINT, raw)
    proceeds = Decimal(_int(quote.get("outAmount"), 0)) / Decimal(1_000_000_000)
    proceeds -= _dec(cfg.get("estimated_exit_fee_sol"), "0.00002")
    cost = _dec(position.get("entry_cost_sol"), 0) * Decimal(raw) / Decimal(remaining)
    net = proceeds - cost
    pct = net / cost * Decimal(100) if cost > 0 else Decimal(0)
    return {"proceeds_sol": proceeds, "cost_sol": cost, "net_sol": net, "net_pct": pct, "sell_raw": raw}


def _close_shadow(app, position: dict, fraction: Decimal, reason: str):
    ev = evaluate_position(app, position, fraction)
    old_raw = max(1, _int(position["token_amount_raw"], 0))
    sold = int(ev["sell_raw"])
    old_cost = _dec(position["entry_cost_sol"], 0)
    cost_fraction = old_cost * Decimal(sold) / Decimal(old_raw)
    remaining = max(0, old_raw - sold)
    remaining_cost = max(Decimal(0), old_cost - cost_fraction)
    realised_total = _dec(position.get("realised_net_sol"), 0) + _dec(ev["net_sol"], 0)
    closed = remaining <= max(1, int(old_raw * .001)) or fraction >= Decimal("0.999")
    now = int(time.time())
    with _DB_LOCK, closing(connect(app)) as conn:
        conn.execute(
            """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,realised_net_sol=?,exit_reason=?,closed_at=?,
                                    status=?,leader_exit_pending=?,updated_at=? WHERE position_id=?""",
            (str(0 if closed else remaining), str(0 if closed else remaining_cost), str(realised_total), reason,
             now if closed else None, "CLOSED" if closed else "OPEN", 0 if closed else int(position.get("leader_exit_pending") or 0), now, position["position_id"]),
        )
        conn.commit()
    return {"closed": closed, "net_sol": ev["net_sol"], "reason": reason}


def process_leader_event(app, event: dict):
    cfg = settings(app)
    actions = []
    for u in all_users(app.csv_dir, enabled_only=True):
        tid = str(u.get("telegram_id") or "")
        if not tid:
            continue
        rank = _leader_rank(app, tid, event["leader_wallet"])
        if rank is None:
            continue
        # Solana deliberately follows the user's main SiBot SHADOW enable switch but never its LIVE signing switch.
        if not _sibot._bool(_sibot.user_settings(app, tid, 0).get("enabled"), False):
            continue
        if event["action"] == "BUY":
            allocation = _dec(cfg.get("shadow_allocation_sol"), ".05")
            try:
                ok, reason, check = _validate_shadow_entry(app, event, allocation, cfg)
                if not ok:
                    actions.append({"telegram_id": tid, "action": "REJECT", "reason": reason})
                    continue
            except Exception as exc:
                actions.append({"telegram_id": tid, "action": "REJECT", "reason": str(exc)})
                continue
            existing = _open_position(app, tid, event["mint"])
            entry_fee = _dec(cfg.get("estimated_entry_fee_sol"), ".00002")
            if existing:
                # A repeated leader BUY is treated as a scale-in step in SHADOW rather than ignored.
                with _DB_LOCK, closing(connect(app)) as conn:
                    conn.execute(
                        """UPDATE positions SET token_amount_raw=?,entry_cost_sol=?,signal_count=signal_count+1,
                                                leader_buy_signature=?,leader_entry_sol=?,leader_entry_token_raw=?,updated_at=?
                           WHERE position_id=?""",
                        (str(_int(existing["token_amount_raw"]) + int(check["out_raw"])),
                         str(_dec(existing["entry_cost_sol"]) + allocation + entry_fee), event["signature"], str(event["sol_amount"]),
                         str(event["token_amount_raw"]), int(time.time()), existing["position_id"]),
                    )
                    conn.commit()
                actions.append({"telegram_id": tid, "action": "SCALE_IN", "position_id": existing["position_id"]})
                continue
            now = int(time.time())
            pid = hashlib.sha256(f"solana|{tid}|{event['leader_wallet']}|{event['mint']}|{event['signature']}".encode()).hexdigest()[:32]
            with _DB_LOCK, closing(connect(app)) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO positions(position_id,telegram_id,leader_wallet,leader_rank,mint,mode,status,token_amount_raw,
                                                        entry_cost_sol,entry_ts,leader_buy_signature,leader_entry_sol,leader_entry_token_raw,
                                                        signal_count,current_exit_sol,unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                                                        leader_exit_pending,realised_net_sol,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, tid, event["leader_wallet"], rank, event["mint"], "SHADOW", "OPEN", str(check["out_raw"]),
                     str(allocation + entry_fee), now, event["signature"], str(event["sol_amount"]), str(event["token_amount_raw"]),
                     1, "0", str(-entry_fee), 0.0, 0.0, 0, "0", now),
                )
                conn.commit()
            actions.append({"telegram_id": tid, "action": "BUY", "position_id": pid, "mode": "SHADOW"})
        elif event["action"] == "SELL":
            with closing(connect(app)) as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM positions WHERE telegram_id=? AND leader_wallet=? AND mint=? AND status='OPEN'",
                    (tid, event["leader_wallet"], event["mint"]),
                ).fetchall()]
            for p in rows:
                full = _float(event.get("sell_pct"), 100) >= 99
                fraction = Decimal(1) if full else max(Decimal("0.0001"), min(Decimal(1), _dec(event.get("sell_pct"), 100) / Decimal(100)))
                if not full and not _bool(cfg.get("mirror_partial_sells"), True):
                    continue
                try:
                    ev = evaluate_position(app, p, fraction)
                    min_profit = _dec(_sibot.user_settings(app, tid, 0).get("min_exit_profit_pct"), ".10")
                    stop = _dec(cfg.get("stop_loss_pct"), 10)
                    if ev["net_pct"] >= min_profit or ev["net_pct"] <= -stop:
                        _close_shadow(app, p, fraction, "SOLANA_LEADER_PARTIAL_SELL" if not full else "SOLANA_LEADER_SELL")
                        actions.append({"telegram_id": tid, "action": "SELL", "position_id": p["position_id"]})
                    else:
                        with _DB_LOCK, closing(connect(app)) as conn:
                            conn.execute("UPDATE positions SET leader_exit_pending=1,updated_at=? WHERE position_id=?", (int(time.time()), p["position_id"]))
                            conn.commit()
                        actions.append({"telegram_id": tid, "action": "EXIT_PENDING", "position_id": p["position_id"]})
                except Exception:
                    continue
    return actions


def _record_leader_event(app, wallet: str, ev: dict):
    eid = hashlib.sha256(f"solana|{wallet}|{ev['signature']}|{ev['action']}|{ev['mint']}".encode()).hexdigest()[:32]
    row = {**ev, "event_id": eid, "leader_wallet": wallet, "created_at": int(time.time())}
    with _DB_LOCK, closing(connect(app)) as conn:
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO leader_events(event_id,leader_wallet,signature,action,mint,decimals,token_amount_raw,sol_amount,
                                                     sell_pct,slot,event_ts,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, wallet, ev["signature"], ev["action"], ev["mint"], ev["decimals"], str(ev["token_amount_raw"]), str(ev["sol_amount"]),
             ev.get("sell_pct"), ev["slot"], ev["event_ts"], row["created_at"]),
        )
        conn.commit()
        inserted = conn.total_changes > before
    return row if inserted else None


def monitor_leaders(app):
    with closing(connect(app)) as conn:
        leaders = [str(r["wallet"]) for r in conn.execute("SELECT DISTINCT wallet FROM leaders").fetchall()]
    events = []
    for wallet in leaders:
        try:
            rows = _get_signatures(app, wallet, 20)
        except Exception:
            continue
        if not rows:
            continue
        with closing(connect(app)) as conn:
            key = f"leader_last_signature:{wallet}"
            last = _state(conn, key, "") or ""
            if not last:
                _set_state(conn, key, str(rows[0].get("signature") or ""))
                continue
        new = []
        for r in rows:
            sig = str(r.get("signature") or "")
            if sig == last:
                break
            new.append(r)
        for r in reversed(new):
            sig = str(r.get("signature") or "")
            if not sig or r.get("err") is not None:
                continue
            try:
                tx = _get_transaction(app, sig)
                ev = classify_swap(tx, wallet) if tx else None
                if ev:
                    saved = _record_leader_event(app, wallet, ev)
                    if saved:
                        events.append(saved)
                        process_leader_event(app, saved)
            except Exception:
                continue
        with closing(connect(app)) as conn:
            _set_state(conn, f"leader_last_signature:{wallet}", str(rows[0].get("signature") or last))
    return events


def monitor_positions(app):
    cfg = settings(app)
    now = int(time.time())
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY updated_at").fetchall()]
    for p in rows:
        try:
            ev = evaluate_position(app, p)
        except Exception:
            continue
        current = _dec(ev["net_pct"])
        peak = max(_dec(p.get("peak_unrealised_pct"), 0), current)
        with _DB_LOCK, closing(connect(app)) as conn:
            conn.execute(
                "UPDATE positions SET current_exit_sol=?,unrealised_net_sol=?,unrealised_pct=?,peak_unrealised_pct=?,updated_at=? WHERE position_id=?",
                (str(ev["proceeds_sol"]), str(ev["net_sol"]), float(current), float(peak), now, p["position_id"]),
            )
            conn.commit()
        reason = None
        if int(p.get("leader_exit_pending") or 0) and current <= -_dec(cfg.get("leader_exit_loss_cap_pct"), "2.5"):
            reason = "SOLANA_LEADER_EXIT_LOSS_CAP"
        elif peak >= _dec(cfg.get("break_even_trigger_pct"), 5) and current <= _dec(cfg.get("break_even_floor_pct"), ".10"):
            reason = "SOLANA_BREAK_EVEN_PROTECT"
        elif peak >= _dec(cfg.get("trailing_trigger_pct"), 10):
            floor = max(_dec(cfg.get("break_even_floor_pct"), ".10"), peak - _dec(cfg.get("trailing_gap_pct"), 5))
            if current <= floor:
                reason = "SOLANA_TRAILING_PROFIT_PROTECT"
        if reason is None and current <= -_dec(cfg.get("stop_loss_pct"), 10):
            reason = "SOLANA_STOP_LOSS"
        if reason is None and current >= _dec(cfg.get("take_profit_pct"), 25):
            reason = "SOLANA_TAKE_PROFIT"
        age_h = Decimal(max(0, now - _int(p.get("entry_ts"), now))) / Decimal(3600)
        if reason is None and age_h >= _dec(cfg.get("max_hold_hours"), 24) and current > 0:
            reason = "SOLANA_MAX_HOLD_PROFIT"
        if reason:
            try:
                fresh = dict(p)
                fresh["peak_unrealised_pct"] = float(peak)
                _close_shadow(app, fresh, Decimal(1), reason)
            except Exception:
                pass
    export_csv(app)


def export_csv(app):
    out = Path(app.csv_dir) / "auto"
    out.mkdir(parents=True, exist_ok=True)
    with closing(connect(app)) as conn:
        rankings = [dict(r) for r in conn.execute("SELECT * FROM rankings ORDER BY telegram_id,rank").fetchall()]
        leaders = [dict(r) for r in conn.execute("SELECT * FROM leaders ORDER BY telegram_id,rank").fetchall()]
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY updated_at DESC").fetchall()]
    _sibot._atomic_csv(out / "sibot_solana_top20.csv", rankings, list(rankings[0].keys()) if rankings else ["telegram_id","lookback_days","rank","wallet","gross_profit_sol","gross_loss_sol","net_profit_sol","wins","losses","closed_trades","win_rate","updated_at"])
    _sibot._atomic_csv(out / "sibot_solana_leaders.csv", leaders, list(leaders[0].keys()) if leaders else ["telegram_id","rank","wallet","net_profit_sol","win_rate","closed_trades","selected_at","updated_at"])
    _sibot._atomic_csv(out / "sibot_solana_positions.csv", positions, list(positions[0].keys()) if positions else ["position_id","telegram_id","leader_wallet","leader_rank","mint","mode","status","token_amount_raw","entry_cost_sol","entry_ts","leader_buy_signature","leader_entry_sol","leader_entry_token_raw","signal_count","current_exit_sol","unrealised_net_sol","unrealised_pct","peak_unrealised_pct","leader_exit_pending","realised_net_sol","exit_signature","exit_reason","closed_at","updated_at"])


def _discovery_worker(app):
    while True:
        cfg = settings(app)
        if _bool(cfg.get("enabled"), True):
            try:
                discover_recent_blocks(app)
                wallet = _next_history_wallet(app)
                if wallet:
                    refresh_wallet_history(app, wallet)
                refresh_rankings(app)
            except Exception as exc:
                print("[sibot-solana-discovery]", type(exc).__name__, exc)
        time.sleep(max(5, _int(cfg.get("discovery_interval_seconds"), 15)))


def _leader_worker(app):
    last_position = 0
    while True:
        cfg = settings(app)
        if _bool(cfg.get("enabled"), True):
            try:
                monitor_leaders(app)
            except Exception as exc:
                print("[sibot-solana-leaders]", type(exc).__name__, exc)
            now = int(time.time())
            if now - last_position >= max(10, _int(cfg.get("position_poll_seconds"), 15)):
                try:
                    monitor_positions(app)
                except Exception as exc:
                    print("[sibot-solana-positions]", type(exc).__name__, exc)
                last_position = now
        time.sleep(max(3, _int(cfg.get("leader_poll_seconds"), 5)))


def start_workers(app):
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
    ensure_settings(app)
    connect(app).close()
    threading.Thread(target=_discovery_worker, args=(app,), daemon=True, name="sibot-solana-discovery").start()
    threading.Thread(target=_leader_worker, args=(app,), daemon=True, name="sibot-solana-leaders").start()
    print("[sibot-solana] finalized-block discovery, 60-day ranking and SHADOW leader monitoring started")
