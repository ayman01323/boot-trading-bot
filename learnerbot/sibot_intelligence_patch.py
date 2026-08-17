from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from . import sibot as _sibot
from .config import load_chains
from .user_registry import all_users

# These controls extend SiBot without changing its existing LIVE signing path.
_INTEL_DEFAULTS = {
    "leader_study_enabled": ("true", "Study recent leader BUY/SELL behaviour before and during copying"),
    "leader_study_trades": ("20", "Matched closed trades retained in the leader behaviour study"),
    "leader_study_refresh_seconds": ("300", "Refresh leader studies and cross-chain profiles"),
    "adaptive_exit_enabled": ("true", "Protect gains with break-even/trailing exits in addition to fixed exits"),
    "break_even_trigger_pct": ("5", "After this peak profit, do not allow the position to fall materially below break-even"),
    "break_even_floor_pct": ("0.10", "Net-profit floor after break-even protection activates"),
    "trailing_trigger_pct": ("10", "Peak profit required before trailing protection activates"),
    "trailing_gap_pct": ("5", "Maximum give-back from peak profit once trailing protection activates"),
    "leader_exit_loss_cap_pct": ("2.5", "Maximum tolerated loss after the copied leader has exited"),
    "pattern_size_low_ratio": ("0.05", "Flag a leader BUY smaller than this fraction of its median historical entry"),
    "pattern_size_high_ratio": ("10", "Flag a leader BUY larger than this multiple of its median historical entry"),
}
for _key, _value in _INTEL_DEFAULTS.items():
    _sibot.DEFAULTS.setdefault(_key, _value)

_INTEL_SPECS = {
    "break_even_trigger_pct": (0.1, 100, "%"),
    "break_even_floor_pct": (0, 100, "%"),
    "trailing_trigger_pct": (0.1, 200, "%"),
    "trailing_gap_pct": (0.1, 100, "%"),
    "leader_exit_loss_cap_pct": (0.1, 50, "%"),
}
for _key, _value in _INTEL_SPECS.items():
    _sibot.SETTING_SPECS.setdefault(_key, _value)

_ORIGINAL_CANDIDATE_WALLETS = _sibot._candidate_wallets
_ORIGINAL_VALIDATE_ENTRY = _sibot._validate_entry
_ORIGINAL_MONITOR_POSITIONS = _sibot.monitor_positions
_INTEL_WORKER_STARTED = False
_INTEL_LOCK = threading.Lock()


def _ensure_schema(app) -> None:
    with closing(_sibot.connect(app)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leader_studies(
              chain_id INTEGER NOT NULL,
              chain_slug TEXT NOT NULL,
              wallet TEXT NOT NULL,
              sample_trades INTEGER NOT NULL DEFAULT 0,
              wins INTEGER NOT NULL DEFAULT 0,
              losses INTEGER NOT NULL DEFAULT 0,
              win_rate REAL NOT NULL DEFAULT 0,
              avg_hold_seconds REAL NOT NULL DEFAULT 0,
              median_hold_seconds REAL NOT NULL DEFAULT 0,
              median_entry_native TEXT NOT NULL DEFAULT '0',
              avg_net_native TEXT NOT NULL DEFAULT '0',
              latest_action TEXT,
              latest_action_tx TEXT,
              latest_action_ts INTEGER,
              latest_token TEXT,
              latest_symbol TEXT,
              latest_native TEXT,
              latest_buy_tx TEXT,
              latest_buy_ts INTEGER,
              latest_buy_token TEXT,
              latest_buy_symbol TEXT,
              latest_buy_native TEXT,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(chain_id,wallet)
            );
            CREATE TABLE IF NOT EXISTS crosschain_profiles(
              telegram_id TEXT NOT NULL,
              wallet TEXT NOT NULL,
              chains_seen INTEGER NOT NULL DEFAULT 0,
              profitable_chains INTEGER NOT NULL DEFAULT 0,
              proven_results INTEGER NOT NULL DEFAULT 0,
              positive_results INTEGER NOT NULL DEFAULT 0,
              positive_ratio REAL NOT NULL DEFAULT 0,
              net_usd REAL NOT NULL DEFAULT 0,
              confidence REAL NOT NULL DEFAULT 0,
              detail_json TEXT NOT NULL DEFAULT '[]',
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(telegram_id,wallet)
            );
            """
        )
        conn.commit()


def _candidate_wallets(app, chain, limit: int) -> list[str]:
    """Always keep current Top-20/leader wallets in the historical backfill queue."""
    preferred = []
    try:
        with closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT wallet,0 priority FROM leaders WHERE chain_id=?
                   UNION ALL
                   SELECT wallet,1 priority FROM rankings WHERE chain_id=?
                   ORDER BY priority""",
                (int(chain.chain_id), int(chain.chain_id)),
            ).fetchall()
            for r in rows:
                wallet = str(r["wallet"] or "").lower()
                if Web3.is_address(wallet) and wallet not in preferred:
                    preferred.append(wallet)
    except Exception:
        pass
    for wallet in _ORIGINAL_CANDIDATE_WALLETS(app, chain, max(limit, 20)):
        if wallet not in preferred:
            preferred.append(wallet)
    return preferred[: max(1, int(limit))]


def _recent_closed_stats(app, chain_id: int, wallet: str, limit: int) -> dict:
    with closing(_sibot.connect(app)) as conn:
        rows = conn.execute(
            """SELECT * FROM wallet_trades
               WHERE chain_id=? AND lower(wallet)=?
               ORDER BY sell_ts DESC LIMIT ?""",
            (int(chain_id), str(wallet).lower(), int(limit)),
        ).fetchall()
    if not rows:
        return {
            "sample_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_hold_seconds": 0.0,
            "median_hold_seconds": 0.0,
            "median_entry_native": Decimal(0),
            "avg_net_native": Decimal(0),
        }
    holds = [max(0, int(r["sell_ts"]) - int(r["buy_ts"])) for r in rows]
    costs = [_sibot._dec(r["cost_native"]) for r in rows if _sibot._dec(r["cost_native"]) > 0]
    nets = [_sibot._dec(r["net_native"]) for r in rows]
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    return {
        "sample_trades": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(rows) * 100.0,
        "avg_hold_seconds": float(sum(holds) / len(holds)) if holds else 0.0,
        "median_hold_seconds": float(statistics.median(holds)) if holds else 0.0,
        "median_entry_native": statistics.median(costs) if costs else Decimal(0),
        "avg_net_native": sum(nets, Decimal(0)) / Decimal(len(nets)) if nets else Decimal(0),
    }


def _latest_direct_actions(app, chain, wallet: str) -> tuple[dict | None, dict | None]:
    """Read a bounded recent Etherscan page and identify the latest direct BUY and latest action.

    This is intentionally the same conservative native<->ERC20 shape used by the existing
    SiBot history reconstruction. If a leader uses an unsupported route the study reports
    no latest direct action rather than inventing one.
    """
    if not getattr(app, "etherscan_api_key", ""):
        return None, None
    try:
        normals = _sibot._etherscan_page(app, chain.chain_id, "txlist", wallet, 1, 160)
        time.sleep(0.22)
        tokens = _sibot._etherscan_page(app, chain.chain_id, "tokentx", wallet, 1, 400)
        time.sleep(0.22)
        internals = _sibot._etherscan_page(app, chain.chain_id, "txlistinternal", wallet, 1, 300)
    except Exception:
        return None, None

    w = wallet.lower()
    normal_map = {
        str(r.get("hash") or "").lower(): r
        for r in normals
        if _sibot._successful_normal(r) and str(r.get("from") or "").lower() == w
    }
    flows = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0, "symbol": "", "decimals": 18}))
    for r in tokens:
        h = str(r.get("hash") or "").lower()
        if h not in normal_map:
            continue
        token = str(r.get("contractAddress") or "").lower()
        if not Web3.is_address(token):
            continue
        raw = _sibot._int(r.get("value"), 0)
        if raw <= 0:
            continue
        f = str(r.get("from") or "").lower()
        t = str(r.get("to") or "").lower()
        x = flows[h][token]
        x["symbol"] = str(r.get("tokenSymbol") or token[:10])[:32]
        x["decimals"] = max(0, min(36, _sibot._int(r.get("tokenDecimal"), 18)))
        if t == w and f != w:
            x["in"] += raw
        if f == w and t != w:
            x["out"] += raw
    internal_in = defaultdict(Decimal)
    for r in internals:
        if str(r.get("isError") or "0") == "1" or str(r.get("to") or "").lower() != w:
            continue
        h = str(r.get("hash") or "").lower()
        if h in normal_map:
            internal_in[h] += Decimal(str(_sibot._int(r.get("value"), 0))) / Decimal(10**18)

    routers = _sibot._routers(app, chain)
    events = []
    for h, tx in normal_map.items():
        to = str(tx.get("to") or "").lower()
        if routers and to not in routers:
            continue
        ts = _sibot._int(tx.get("timeStamp"), 0)
        value = Decimal(str(_sibot._int(tx.get("value"), 0))) / Decimal(10**18)
        token_items = []
        for token, f in flows.get(h, {}).items():
            net = int(f["in"]) - int(f["out"])
            if net:
                token_items.append((token, net, f))
        positive = [x for x in token_items if x[1] > 0]
        negative = [x for x in token_items if x[1] < 0]
        if value > 0 and len(positive) == 1 and not negative:
            token, raw, meta = positive[0]
            native = max(Decimal(0), value - internal_in.get(h, Decimal(0)))
            if native > 0:
                events.append({"action": "BUY", "tx": h, "ts": ts, "token": token, "symbol": meta["symbol"], "native": native, "raw": raw})
        elif value == 0 and len(negative) == 1 and not positive and internal_in.get(h, Decimal(0)) > 0:
            token, raw, meta = negative[0]
            events.append({"action": "SELL", "tx": h, "ts": ts, "token": token, "symbol": meta["symbol"], "native": internal_in[h], "raw": abs(raw)})
    events.sort(key=lambda x: x["ts"], reverse=True)
    latest = events[0] if events else None
    latest_buy = next((x for x in events if x["action"] == "BUY"), None)
    return latest, latest_buy


def refresh_one_study(app, chain, wallet: str, fetch_remote=True) -> dict:
    _ensure_schema(app)
    cfg = _sibot.platform_settings(app, chain.chain_id)
    sample_n = max(3, min(100, _sibot._int(cfg.get("leader_study_trades"), 20)))
    stats = _recent_closed_stats(app, chain.chain_id, wallet, sample_n)
    latest = latest_buy = None
    if fetch_remote:
        latest, latest_buy = _latest_direct_actions(app, chain, wallet)
    if latest is None or latest_buy is None:
        with closing(_sibot.connect(app)) as conn:
            previous = conn.execute(
                "SELECT * FROM leader_studies WHERE chain_id=? AND wallet=?",
                (int(chain.chain_id), wallet.lower()),
            ).fetchone()
        if previous:
            if latest is None and previous["latest_action"]:
                latest = {
                    "action": previous["latest_action"], "tx": previous["latest_action_tx"],
                    "ts": previous["latest_action_ts"], "token": previous["latest_token"],
                    "symbol": previous["latest_symbol"], "native": _sibot._dec(previous["latest_native"]),
                }
            if latest_buy is None and previous["latest_buy_tx"]:
                latest_buy = {
                    "action": "BUY", "tx": previous["latest_buy_tx"], "ts": previous["latest_buy_ts"],
                    "token": previous["latest_buy_token"], "symbol": previous["latest_buy_symbol"],
                    "native": _sibot._dec(previous["latest_buy_native"]),
                }
    now = int(time.time())
    row = {
        "chain_id": chain.chain_id, "chain_slug": chain.slug, "wallet": wallet.lower(),
        **stats,
        "latest_action": (latest or {}).get("action"),
        "latest_action_tx": (latest or {}).get("tx"),
        "latest_action_ts": (latest or {}).get("ts"),
        "latest_token": (latest or {}).get("token"),
        "latest_symbol": (latest or {}).get("symbol"),
        "latest_native": str((latest or {}).get("native") or 0),
        "latest_buy_tx": (latest_buy or {}).get("tx"),
        "latest_buy_ts": (latest_buy or {}).get("ts"),
        "latest_buy_token": (latest_buy or {}).get("token"),
        "latest_buy_symbol": (latest_buy or {}).get("symbol"),
        "latest_buy_native": str((latest_buy or {}).get("native") or 0),
        "updated_at": now,
    }
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO leader_studies(
                 chain_id,chain_slug,wallet,sample_trades,wins,losses,win_rate,avg_hold_seconds,
                 median_hold_seconds,median_entry_native,avg_net_native,latest_action,latest_action_tx,
                 latest_action_ts,latest_token,latest_symbol,latest_native,latest_buy_tx,latest_buy_ts,
                 latest_buy_token,latest_buy_symbol,latest_buy_native,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chain_id,wallet) DO UPDATE SET
                 chain_slug=excluded.chain_slug,sample_trades=excluded.sample_trades,wins=excluded.wins,
                 losses=excluded.losses,win_rate=excluded.win_rate,avg_hold_seconds=excluded.avg_hold_seconds,
                 median_hold_seconds=excluded.median_hold_seconds,median_entry_native=excluded.median_entry_native,
                 avg_net_native=excluded.avg_net_native,latest_action=excluded.latest_action,
                 latest_action_tx=excluded.latest_action_tx,latest_action_ts=excluded.latest_action_ts,
                 latest_token=excluded.latest_token,latest_symbol=excluded.latest_symbol,
                 latest_native=excluded.latest_native,latest_buy_tx=excluded.latest_buy_tx,
                 latest_buy_ts=excluded.latest_buy_ts,latest_buy_token=excluded.latest_buy_token,
                 latest_buy_symbol=excluded.latest_buy_symbol,latest_buy_native=excluded.latest_buy_native,
                 updated_at=excluded.updated_at""",
            (
                row["chain_id"], row["chain_slug"], row["wallet"], row["sample_trades"], row["wins"], row["losses"],
                row["win_rate"], row["avg_hold_seconds"], row["median_hold_seconds"], str(row["median_entry_native"]),
                str(row["avg_net_native"]), row["latest_action"], row["latest_action_tx"], row["latest_action_ts"],
                row["latest_token"], row["latest_symbol"], row["latest_native"], row["latest_buy_tx"],
                row["latest_buy_ts"], row["latest_buy_token"], row["latest_buy_symbol"], row["latest_buy_native"], now,
            ),
        )
        conn.commit()
    return row


def study_row(app, chain_id: int, wallet: str) -> dict | None:
    _ensure_schema(app)
    with closing(_sibot.connect(app)) as conn:
        r = conn.execute(
            "SELECT * FROM leader_studies WHERE chain_id=? AND wallet=?",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
        return dict(r) if r else None


def refresh_leader_studies(app, fetch_remote=True) -> None:
    _ensure_schema(app)
    chains = {c.chain_id: c for c in load_chains(app, enabled_only=True)}
    with closing(_sibot.connect(app)) as conn:
        leaders = conn.execute("SELECT DISTINCT chain_id,wallet FROM leaders ORDER BY chain_id,wallet").fetchall()
    for r in leaders:
        chain = chains.get(int(r["chain_id"]))
        if not chain:
            continue
        try:
            # Force leaders into the normal bounded historical refresh queue as well.
            _sibot._REFRESH_NOW.add(str(r["wallet"]).lower())
            refresh_one_study(app, chain, str(r["wallet"]), fetch_remote=fetch_remote)
        except Exception as exc:
            print(f"[sibot-study:{chain.slug}]", type(exc).__name__, exc)


def _chain_evidence(app, chain, wallet: str, cutoff: int) -> dict:
    path = Path(app.data_dir) / f"{chain.slug}.sqlite3"
    result = {
        "chain_id": chain.chain_id, "chain_slug": chain.slug, "chain_name": chain.name,
        "proven": 0, "positive": 0, "net_usd": 0.0, "net_native": None, "rank": None,
    }
    if path.exists():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT COUNT(*) proven,
                          SUM(CASE WHEN COALESCE(profit_base,0)>0 THEN 1 ELSE 0 END) positive,
                          COALESCE(SUM(COALESCE(profit_usd,0)),0) net_usd
                   FROM trade_behaviour_evidence
                   WHERE lower(wallet)=? AND block_timestamp>=? AND proof_quality='PROVEN_WRAPPED_BASE'""",
                (wallet.lower(), int(cutoff)),
            ).fetchone()
            if row:
                result["proven"] = int(row["proven"] or 0)
                result["positive"] = int(row["positive"] or 0)
                result["net_usd"] = float(row["net_usd"] or 0)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return result


def crosschain_profile(app, telegram_id, wallet: str, lookback_days=60) -> dict:
    """Build one EVM identity profile for the same 0x address across enabled chains."""
    _ensure_schema(app)
    wallet = str(wallet or "").lower()
    if not Web3.is_address(wallet):
        raise ValueError("Cross-chain EVM profile requires a valid 0x address")
    cutoff = int(time.time()) - max(1, int(lookback_days)) * 86400
    details = []
    with closing(_sibot.connect(app)) as conn:
        ranking_map = {
            int(r["chain_id"]): dict(r)
            for r in conn.execute(
                "SELECT * FROM rankings WHERE telegram_id=? AND lower(wallet)=?",
                (str(telegram_id), wallet),
            ).fetchall()
        }
    for chain in load_chains(app, enabled_only=True):
        d = _chain_evidence(app, chain, wallet, cutoff)
        rr = ranking_map.get(chain.chain_id)
        if rr:
            d["rank"] = int(rr["rank"])
            d["net_native"] = str(rr["net_profit_native"])
            if d["proven"] == 0:
                d["proven"] = int(rr["closed_trades"] or 0)
                d["positive"] = int(rr["wins"] or 0)
        if d["proven"] > 0 or rr:
            details.append(d)

    seen = len(details)
    profitable = sum(1 for d in details if d["net_usd"] > 0 or (d["net_usd"] == 0 and d.get("net_native") and _sibot._dec(d["net_native"]) > 0))
    proven = sum(int(d["proven"]) for d in details)
    positive = sum(int(d["positive"]) for d in details)
    ratio = positive / proven * 100.0 if proven else 0.0
    net_usd = sum(float(d["net_usd"]) for d in details)
    breadth_score = min(15.0, seen * 3.0)
    consistency_score = 35.0 * profitable / seen if seen else 0.0
    evidence_score = min(25.0, proven * 1.25)
    positive_score = min(25.0, max(0.0, ratio) * 0.25)
    confidence = min(100.0, breadth_score + consistency_score + evidence_score + positive_score)
    profile = {
        "telegram_id": str(telegram_id), "wallet": wallet, "chains_seen": seen,
        "profitable_chains": profitable, "proven_results": proven, "positive_results": positive,
        "positive_ratio": ratio, "net_usd": net_usd, "confidence": confidence,
        "details": details, "updated_at": int(time.time()),
    }
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute(
            """INSERT INTO crosschain_profiles(telegram_id,wallet,chains_seen,profitable_chains,proven_results,
                                                positive_results,positive_ratio,net_usd,confidence,detail_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(telegram_id,wallet) DO UPDATE SET
                 chains_seen=excluded.chains_seen,profitable_chains=excluded.profitable_chains,
                 proven_results=excluded.proven_results,positive_results=excluded.positive_results,
                 positive_ratio=excluded.positive_ratio,net_usd=excluded.net_usd,confidence=excluded.confidence,
                 detail_json=excluded.detail_json,updated_at=excluded.updated_at""",
            (str(telegram_id), wallet, seen, profitable, proven, positive, ratio, net_usd, confidence, json.dumps(details), profile["updated_at"]),
        )
        conn.commit()
    return profile


def refresh_crosschain_profiles(app) -> None:
    _ensure_schema(app)
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    with closing(_sibot.connect(app)) as conn:
        for u in users:
            tid = str(u.get("telegram_id") or "")
            if not tid:
                continue
            wallets = [str(r["wallet"]) for r in conn.execute(
                "SELECT DISTINCT wallet FROM rankings WHERE telegram_id=?", (tid,)
            ).fetchall()]
            for wallet in wallets:
                try:
                    crosschain_profile(app, tid, wallet)
                except Exception:
                    continue
    export_intelligence(app)


def profile_rows(app, telegram_id) -> list[dict]:
    _ensure_schema(app)
    with closing(_sibot.connect(app)) as conn:
        rows = conn.execute(
            """SELECT * FROM crosschain_profiles WHERE telegram_id=?
               ORDER BY confidence DESC,profitable_chains DESC,proven_results DESC LIMIT 50""",
            (str(telegram_id),),
        ).fetchall()
        return [dict(r) for r in rows]


def wallet_kind_by_chain(app, wallet: str) -> list[dict]:
    out = []
    for chain in load_chains(app, enabled_only=True):
        kind = "UNKNOWN"
        try:
            w3 = _sibot._rpc(chain)
            code = bytes(w3.eth.get_code(Web3.to_checksum_address(wallet)))
            kind = "EOA" if len(code) == 0 else "CONTRACT"
        except Exception:
            pass
        out.append({"chain_id": chain.chain_id, "chain_slug": chain.slug, "kind": kind})
    return out


def _validate_entry(app, trader, event: dict, amount: Decimal, cfg: dict, live: bool):
    ok, reason, check = _ORIGINAL_VALIDATE_ENTRY(app, trader, event, amount, cfg, live)
    if not ok:
        return ok, reason, check
    study = study_row(app, trader.chain.chain_id, event.get("leader_wallet") or "")
    if study and int(study.get("sample_trades") or 0) >= 3:
        median = _sibot._dec(study.get("median_entry_native"), 0)
        leader_amount = _sibot._dec(event.get("native_amount"), 0)
        ratio = leader_amount / median if median > 0 and leader_amount > 0 else Decimal(0)
        low = _sibot._dec(cfg.get("pattern_size_low_ratio"), "0.05")
        high = _sibot._dec(cfg.get("pattern_size_high_ratio"), "10")
        if ratio and ratio < low:
            pattern = "VERY_SMALL_VS_HISTORY"
        elif ratio and ratio > high:
            pattern = "VERY_LARGE_VS_HISTORY"
        else:
            pattern = "NORMAL_SIZE" if ratio else "SIZE_UNKNOWN"
        check = dict(check)
        check.update({
            "leader_study_samples": int(study.get("sample_trades") or 0),
            "leader_study_win_rate": float(study.get("win_rate") or 0),
            "leader_study_avg_hold_seconds": float(study.get("avg_hold_seconds") or 0),
            "leader_entry_size_ratio": ratio,
            "leader_entry_pattern": pattern,
        })
    return True, "PASS", check


def _adaptive_exit_reason(cfg: dict, position: dict, evaluation: dict) -> str | None:
    if not _sibot._bool(cfg.get("adaptive_exit_enabled"), True):
        return None
    net_pct = _sibot._dec(evaluation.get("net_pct"), 0)
    peak = max(_sibot._dec(position.get("peak_unrealised_pct"), 0), net_pct)
    if _sibot._bool(position.get("leader_exit_pending"), False):
        loss_cap = _sibot._dec(cfg.get("leader_exit_loss_cap_pct"), "2.5")
        if net_pct <= -loss_cap:
            return "LEADER_EXIT_LOSS_CAP"
    be_trigger = _sibot._dec(cfg.get("break_even_trigger_pct"), 5)
    be_floor = _sibot._dec(cfg.get("break_even_floor_pct"), ".10")
    if peak >= be_trigger and net_pct <= be_floor:
        return "BREAK_EVEN_PROTECT"
    trail_trigger = _sibot._dec(cfg.get("trailing_trigger_pct"), 10)
    gap = _sibot._dec(cfg.get("trailing_gap_pct"), 5)
    if peak >= trail_trigger:
        floor = max(be_floor, peak - gap)
        if net_pct <= floor:
            return "TRAILING_PROFIT_PROTECT"
    return None


def monitor_positions(app) -> None:
    """Preserve existing exits, then add adaptive loss/profit protection to remaining positions."""
    _ORIGINAL_MONITOR_POSITIONS(app)
    with closing(_sibot.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY updated_at").fetchall()]
    for p in rows:
        cfg = _sibot.user_settings(app, p["telegram_id"], p["chain_id"])
        try:
            ev = _sibot.evaluate_position(app, p)
            reason = _adaptive_exit_reason(cfg, p, ev)
            if reason:
                _sibot.close_position(app, p["position_id"], Decimal(1), reason)
        except Exception:
            continue
    _sibot.export_positions(app)


def export_intelligence(app) -> None:
    out = Path(app.csv_dir) / "auto"
    out.mkdir(parents=True, exist_ok=True)
    with closing(_sibot.connect(app)) as conn:
        studies = [dict(r) for r in conn.execute("SELECT * FROM leader_studies ORDER BY chain_id,wallet").fetchall()]
        profiles = [dict(r) for r in conn.execute("SELECT * FROM crosschain_profiles ORDER BY telegram_id,confidence DESC").fetchall()]
    study_headers = list(studies[0].keys()) if studies else [
        "chain_id","chain_slug","wallet","sample_trades","wins","losses","win_rate","avg_hold_seconds",
        "median_hold_seconds","median_entry_native","avg_net_native","latest_action","latest_action_tx","latest_action_ts",
        "latest_token","latest_symbol","latest_native","latest_buy_tx","latest_buy_ts","latest_buy_token","latest_buy_symbol",
        "latest_buy_native","updated_at"
    ]
    profile_headers = list(profiles[0].keys()) if profiles else [
        "telegram_id","wallet","chains_seen","profitable_chains","proven_results","positive_results","positive_ratio",
        "net_usd","confidence","detail_json","updated_at"
    ]
    _sibot._atomic_csv(out / "sibot_leader_studies.csv", studies, study_headers)
    _sibot._atomic_csv(out / "sibot_crosschain_profiles.csv", profiles, profile_headers)


def _worker(app):
    while True:
        try:
            refresh_leader_studies(app, fetch_remote=True)
            refresh_crosschain_profiles(app)
        except Exception as exc:
            print("[sibot-intelligence]", type(exc).__name__, exc)
        try:
            seconds = max(60, _sibot._int(_sibot.platform_settings(app, 0).get("leader_study_refresh_seconds"), 300))
        except Exception:
            seconds = 300
        time.sleep(seconds)


def start_workers(app) -> None:
    global _INTEL_WORKER_STARTED
    with _INTEL_LOCK:
        if _INTEL_WORKER_STARTED:
            return
        _INTEL_WORKER_STARTED = True
    _ensure_schema(app)
    threading.Thread(target=_worker, args=(app,), daemon=True, name="sibot-intelligence").start()
    print("[sibot-intelligence] leader study, cross-chain profile and adaptive exit layer started")


def install() -> None:
    if getattr(_sibot, "_intelligence_patch_installed", False):
        return
    _ensure_defaults = _sibot.ensure_settings
    _sibot._candidate_wallets = _candidate_wallets
    _sibot._validate_entry = _validate_entry
    _sibot.monitor_positions = monitor_positions
    # Trigger settings creation/migration later when an app instance exists.
    _sibot._intelligence_patch_installed = True


install()
