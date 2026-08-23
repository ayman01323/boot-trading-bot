#!/usr/bin/env python3
"""Low-memory, read-only SiBot leader-gate/history proof.

This diagnostic intentionally does not import the learnerbot runtime patch chain.
On the production 1 GiB VPS that import graph can materially increase RSS while the
live bot is already memory constrained. The report reads only the isolated SQLite
snapshots prepared by /usr/local/sbin/run-sibot-leader-gate-report and the copied CSV
settings. It never calls Alchemy, Etherscan, Solana RPC, Telegram, signing or trading.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import sys
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

if os.getenv("SIBOT_GATE_SNAPSHOT") != "1":
    print(
        "Refusing to run SiBot leader-gate report outside the isolated snapshot. "
        "Use /usr/local/sbin/run-sibot-leader-gate-report via the GitHub workflow.",
        file=sys.stderr,
    )
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR") or (ROOT / "data"))
CSV_DIR = Path(os.environ.get("CSV_DIR") or (ROOT / "CSVbot"))
CANDIDATE_CAP = 5
PROOF_CHAIN_IDS = {56, 42161}
EVM_FALLBACK_NAMES = {
    1: "Ethereum",
    56: "BNB Smart Chain",
    137: "Polygon PoS",
    8453: "Base",
    42161: "Arbitrum One",
}
EVM_FLOORS = {
    "min_closed_trades": Decimal("50"),
    "min_win_rate_pct": Decimal("55"),
    "min_profit_factor": Decimal("1.5"),
    "min_recent_win_rate_pct": Decimal("55"),
    "min_recent_profit_factor": Decimal("1.10"),
    "max_leader_drawdown_pct": Decimal("20"),
}
SOLANA_FLOORS = {
    "min_win_rate_pct": Decimal("65"),
    "min_profit_factor": Decimal("1.75"),
    "min_recent_win_rate_pct": Decimal("65"),
    "min_recent_profit_factor": Decimal("1.50"),
    "max_leader_drawdown_pct": Decimal("20"),
}


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _iso(value) -> str:
    ts = _int(value, 0)
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage_days(start, end) -> float:
    a, b = _int(start, 0), _int(end, 0)
    if not a or not b or b < a:
        return 0.0
    return (b - a) / 86400.0


def _short(value: object) -> str:
    text = str(value or "")
    return text if len(text) <= 14 else f"{text[:7]}…{text[-5:]}"


def _safe_error(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return "<none>"
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,&]+", r"\1<redacted>", text)
    text = re.sub(r"\b(sk|gh[opusr]?|github_pat)_[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
    return re.sub(r"\s+", " ", text)[:limit]


def _readonly(path: Path) -> sqlite3.Connection:
    p = path.expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    conn = sqlite3.connect(f"file:{quote(p.as_posix(), safe='/')}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _one(conn: sqlite3.Connection, sql: str, params=()) -> dict:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except sqlite3.Error as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    except Exception:
        return []


def _boolish(value, default=True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "on", "y"}


def _evm_chain_names(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for row in _csv_rows(CSV_DIR / "chains.csv"):
        cid = _int(row.get("chain_id"), 0)
        typ = str(row.get("type") or "EVM").strip().lower()
        if cid and typ == "evm" and _boolish(row.get("enabled"), True):
            seen[cid] = str(row.get("name") or row.get("slug") or EVM_FALLBACK_NAMES.get(cid) or cid)
    try:
        for row in conn.execute("SELECT DISTINCT chain_id,chain_slug FROM rankings ORDER BY chain_id"):
            cid = _int(row["chain_id"], 0)
            if cid:
                seen.setdefault(cid, EVM_FALLBACK_NAMES.get(cid) or str(row["chain_slug"] or cid))
    except sqlite3.Error:
        pass
    return sorted(seen.items(), key=lambda item: (0 if item[0] in PROOF_CHAIN_IDS else 1, item[0]))


def _platform_evm_cfg(chain_id: int) -> dict:
    cfg = {
        "lookback_days": "60",
        "recent_trade_window": "20",
        "min_closed_trades": "50",
        "min_win_rate_pct": "55",
        "min_profit_factor": "1.5",
        "min_recent_win_rate_pct": "55",
        "min_recent_profit_factor": "1.10",
        "max_leader_drawdown_pct": "20",
        "require_complete_history": "false",
    }
    rows = _csv_rows(CSV_DIR / "sibot_settings.csv")
    for scope in (0, int(chain_id)):
        for row in rows:
            if _int(row.get("chain_id"), 0) != scope:
                continue
            key = str(row.get("setting") or "").strip()
            if key:
                cfg[key] = str(row.get("value") or "").strip()
    cfg["require_complete_history"] = "false"
    for key, floor in EVM_FLOORS.items():
        if key == "max_leader_drawdown_pct":
            cfg[key] = str(min(_dec(cfg.get(key), floor), floor))
        else:
            cfg[key] = str(max(_dec(cfg.get(key), floor), floor))
    return cfg


def _pf(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def _drawdown(rows: list[dict], cost_key: str, net_key: str) -> Decimal:
    equity = Decimal(1)
    peak = Decimal(1)
    worst = Decimal(0)
    for row in rows:
        cost = _dec(row.get(cost_key), 0)
        net = _dec(row.get(net_key), 0)
        if cost <= 0:
            continue
        ret = max(Decimal("-0.95"), min(Decimal("5"), net / cost))
        equity *= Decimal(1) + ret
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * Decimal(100))
    return worst


def _stats(rows: list[dict], cost_key: str, net_key: str) -> dict:
    profit = sum((_dec(r.get(net_key)) for r in rows if _dec(r.get(net_key)) > 0), Decimal(0))
    loss = sum((-_dec(r.get(net_key)) for r in rows if _dec(r.get(net_key)) < 0), Decimal(0))
    wins = sum(1 for r in rows if _dec(r.get(net_key)) > 0)
    closed = len(rows)
    return {
        "profit": profit,
        "loss": loss,
        "net": profit - loss,
        "closed": closed,
        "win_rate": Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0),
        "profit_factor": _pf(profit, loss),
        "drawdown_pct": _drawdown(rows, cost_key, net_key),
    }


def _evm_metrics(conn: sqlite3.Connection, chain_id: int, wallet: str, cfg: dict) -> dict:
    lookback = max(1, min(365, _int(cfg.get("lookback_days"), 60)))
    recent_n = max(5, min(100, _int(cfg.get("recent_trade_window"), 20)))
    cutoff = int(time.time()) - lookback * 86400
    rows = [dict(r) for r in conn.execute(
        "SELECT cost_native,net_native,sell_ts FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=? ORDER BY sell_ts",
        (int(chain_id), str(wallet).lower(), cutoff),
    ).fetchall()]
    overall = _stats(rows, "cost_native", "net_native")
    recent = _stats(rows[-recent_n:], "cost_native", "net_native")
    overall["recent_win_rate"] = recent["win_rate"]
    overall["recent_profit_factor"] = recent["profit_factor"]
    return overall


def _evm_stage(metrics: dict, cfg: dict) -> str:
    if _int(metrics.get("closed")) < max(1, _int(cfg.get("min_closed_trades"), 50)):
        return "closed_trades"
    if _dec(metrics.get("win_rate")) < _dec(cfg.get("min_win_rate_pct"), 55):
        return "historical_win_rate"
    if _dec(metrics.get("profit_factor")) < _dec(cfg.get("min_profit_factor"), "1.5"):
        return "profit_factor"
    if _dec(metrics.get("drawdown_pct")) > _dec(cfg.get("max_leader_drawdown_pct"), 20):
        return "drawdown"
    if _dec(metrics.get("recent_win_rate")) < _dec(cfg.get("min_recent_win_rate_pct"), 55):
        return "recent_win_rate"
    if _dec(metrics.get("recent_profit_factor")) < _dec(cfg.get("min_recent_profit_factor"), "1.10"):
        return "recent_profit_factor"
    if _dec(metrics.get("net")) <= 0:
        return "positive_net"
    return "PASS"


def report_evm(conn: sqlite3.Connection) -> None:
    print("=== EVM SiBot low-memory leader gate + reconstruction proof ===")
    print(f"candidate_cap_per_chain: {CANDIDATE_CAP}")
    print("network_calls: 0")
    print("settings_basis: copied platform settings + final hard floors; per-user stricter overrides are not used to relax any floor")
    for chain_id, chain_name in _evm_chain_names(conn):
        cfg = _platform_evm_cfg(chain_id)
        tid_row = _one(conn, "SELECT telegram_id,COUNT(*) n FROM rankings WHERE chain_id=? GROUP BY telegram_id ORDER BY n DESC,telegram_id LIMIT 1", (chain_id,))
        tid = str(tid_row.get("telegram_id") or "")
        eligible = _one(conn, "SELECT COUNT(*) n FROM rankings WHERE chain_id=? AND telegram_id=?", (chain_id, tid)) if tid else {"n": 0}
        rankings = [dict(r) for r in conn.execute(
            "SELECT * FROM rankings WHERE chain_id=? AND telegram_id=? ORDER BY rank LIMIT ?",
            (chain_id, tid, CANDIDATE_CAP),
        ).fetchall()] if tid else []
        status = _one(conn, """SELECT COUNT(*) n,
            SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) complete,
            SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,
            MIN(fetched_at) oldest,MAX(fetched_at) newest,
            MIN(coverage_start_ts) coverage_start,MAX(coverage_end_ts) coverage_end
            FROM wallet_history_status WHERE chain_id=?""", (chain_id,))
        trades = _one(conn, """SELECT COUNT(*) n,COUNT(DISTINCT lower(wallet)) wallets,
            MIN(sell_ts) first_close,MAX(sell_ts) last_close FROM wallet_trades WHERE chain_id=?""", (chain_id,))
        dominant = _one(conn, """SELECT error,COUNT(*) n FROM wallet_history_status
            WHERE chain_id=? AND COALESCE(error,'')<>'' GROUP BY error ORDER BY n DESC LIMIT 1""", (chain_id,))
        print(f"\n[{chain_name} chain_id={chain_id}]")
        print(f"  eligible_candidates={_int(eligible.get('n'))} processed_candidates={len(rankings)} cap={CANDIDATE_CAP}")
        print(
            "  history_store: "
            f"status_wallets={_int(status.get('n'))} complete={_int(status.get('complete'))} errors={_int(status.get('errors'))} "
            f"oldest_fetch={_iso(status.get('oldest'))} newest_fetch={_iso(status.get('newest'))} "
            f"coverage={_coverage_days(status.get('coverage_start'), status.get('coverage_end')):.1f}d "
            f"wallet_trades={_int(trades.get('n'))} wallets_with_trades={_int(trades.get('wallets'))} "
            f"first_reconstructed_close={_iso(trades.get('first_close'))} last_reconstructed_close={_iso(trades.get('last_close'))}"
        )
        print(f"  dominant_history_error: count={_int(dominant.get('n'))} reason={_safe_error(dominant.get('error') or dominant.get('_error'))}")
        any_candidate_reconstructed = False
        for ranking in rankings:
            wallet = str(ranking.get("wallet") or "")
            metrics = _evm_metrics(conn, chain_id, wallet, cfg)
            hs = _one(conn, """SELECT fetched_at,coverage_start_ts,coverage_end_ts,history_complete,
                unmatched_sells,normal_rows,token_rows,internal_rows,error
                FROM wallet_history_status WHERE chain_id=? AND lower(wallet)=?""", (chain_id, wallet.lower()))
            reconstructed = _int(metrics.get("closed"))
            any_candidate_reconstructed = any_candidate_reconstructed or reconstructed > 0
            print(
                f"  candidate #{ranking.get('rank','?')} {_short(wallet)} stage={_evm_stage(metrics, cfg)} "
                f"ranking_closed={_int(ranking.get('closed_trades'))} reconstructed_{_int(cfg.get('lookback_days'),60)}d={reconstructed} "
                f"fetch={_iso(hs.get('fetched_at'))} history_complete={bool(_int(hs.get('history_complete')))} "
                f"rows(normal/token/internal)={_int(hs.get('normal_rows'))}/{_int(hs.get('token_rows'))}/{_int(hs.get('internal_rows'))} "
                f"history_error={_safe_error(hs.get('error') or hs.get('_error'))}"
            )
        if chain_id in PROOF_CHAIN_IDS:
            store_any = _int(trades.get("n")) > 0
            proof = "PASS" if any_candidate_reconstructed else ("PARTIAL_STORE_ONLY" if store_any else "FAIL_ZERO_RECONSTRUCTION")
            print(f"  BOUNDED_PROOF_RESULT={proof} candidate_reconstruction_any={str(any_candidate_reconstructed).lower()} store_reconstruction_any={str(store_any).lower()}")


def _solana_cfg() -> dict:
    cfg = {
        "lookback_days": "60", "recent_trade_window": "20", "min_closed_trades": "5",
        "min_win_rate_pct": "65", "min_profit_factor": "1.75", "min_recent_win_rate_pct": "65",
        "min_recent_profit_factor": "1.50", "max_leader_drawdown_pct": "20", "require_complete_history": "false",
    }
    for row in _csv_rows(CSV_DIR / "solana_settings.csv"):
        key = str(row.get("setting") or "").strip()
        if key:
            cfg[key] = str(row.get("value") or "").strip()
    cfg["require_complete_history"] = "false"
    for key, floor in SOLANA_FLOORS.items():
        if key == "max_leader_drawdown_pct":
            cfg[key] = str(min(_dec(cfg.get(key), floor), floor))
        else:
            cfg[key] = str(max(_dec(cfg.get(key), floor), floor))
    return cfg


def report_solana() -> None:
    path = DATA_DIR / "solana_sibot.sqlite3"
    if not path.is_file():
        print("\n=== Solana summary ===\nmissing solana_sibot.sqlite3")
        return
    cfg = _solana_cfg()
    cutoff = int(time.time()) - max(1, _int(cfg.get("lookback_days"), 60)) * 86400
    recent_n = max(5, min(100, _int(cfg.get("recent_trade_window"), 20)))
    with _readonly(path) as conn:
        status = _one(conn, """SELECT COUNT(*) n,
            SUM(CASE WHEN truncated=0 AND COALESCE(error,'')='' THEN 1 ELSE 0 END) complete,
            SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,
            MIN(fetched_at) oldest,MAX(fetched_at) newest,SUM(signatures) signatures,SUM(swaps) swaps,SUM(closed_trades) closes
            FROM history_status""")
        discovered = _one(conn, "SELECT COUNT(*) n,SUM(swap_events) events,MIN(first_seen) first_seen,MAX(last_seen) last_seen FROM candidates")
        worker_last_run = _one(conn, "SELECT value FROM state WHERE key='worker:history:last_run'").get("value", 0)
        worker_last_success = _one(conn, "SELECT value FROM state WHERE key='worker:history:last_success'").get("value", 0)
        worker_last_error = _one(conn, "SELECT value FROM state WHERE key='worker:history:last_error'").get("value", "")
        tid_row = _one(conn, "SELECT telegram_id,COUNT(*) n FROM rankings GROUP BY telegram_id ORDER BY n DESC,telegram_id LIMIT 1")
        tid = str(tid_row.get("telegram_id") or "")
        eligible = _one(conn, "SELECT COUNT(*) n FROM rankings WHERE telegram_id=?", (tid,)) if tid else {"n": 0}
        rankings = [dict(r) for r in conn.execute("SELECT * FROM rankings WHERE telegram_id=? ORDER BY rank LIMIT ?", (tid, CANDIDATE_CAP)).fetchall()] if tid else []
        print("\n=== Solana low-memory stored-evidence summary ===")
        print(f"  eligible_candidates={_int(eligible.get('n'))} processed_candidates={len(rankings)} cap={CANDIDATE_CAP}")
        print(
            f"  history_store: candidates={_int(discovered.get('n'))} discovery_swap_events={_int(discovered.get('events'))} "
            f"status_wallets={_int(status.get('n'))} complete={_int(status.get('complete'))} errors={_int(status.get('errors'))} "
            f"newest_fetch={_iso(status.get('newest'))} signatures={_int(status.get('signatures'))} swaps={_int(status.get('swaps'))} closed_trades={_int(status.get('closes'))}"
        )
        print(
            "  history worker marker: "
            f"last_run={_iso(worker_last_run)} worker:history:last_success={_iso(worker_last_success)} "
            f"last_error={_safe_error(worker_last_error)} retention=LATEST_ONLY"
        )
        for ranking in rankings:
            wallet = str(ranking.get("wallet") or "")
            rows = [dict(r) for r in conn.execute("SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts", (wallet, cutoff)).fetchall()]
            overall = _stats(rows, "cost_sol", "net_sol")
            recent = _stats(rows[-recent_n:], "cost_sol", "net_sol")
            hs = _one(conn, "SELECT fetched_at,truncated,error,signatures,swaps,closed_trades FROM history_status WHERE wallet=?", (wallet,))
            stage = "PASS"
            if overall["closed"] < max(1, _int(cfg.get("min_closed_trades"), 5)):
                stage = "closed_trades"
            elif overall["win_rate"] < _dec(cfg.get("min_win_rate_pct"), 65):
                stage = "historical_win_rate"
            elif overall["profit_factor"] < _dec(cfg.get("min_profit_factor"), "1.75"):
                stage = "profit_factor"
            elif overall["drawdown_pct"] > _dec(cfg.get("max_leader_drawdown_pct"), 20):
                stage = "drawdown"
            elif recent["win_rate"] < _dec(cfg.get("min_recent_win_rate_pct"), 65):
                stage = "recent_win_rate"
            elif recent["profit_factor"] < _dec(cfg.get("min_recent_profit_factor"), "1.50"):
                stage = "recent_profit_factor"
            elif overall["net"] <= 0:
                stage = "positive_net"
            print(
                f"  candidate #{ranking.get('rank','?')} {_short(wallet)} stage={stage} ranking_closed={_int(ranking.get('closed_trades'))} "
                f"reconstructed={overall['closed']} fetch={_iso(hs.get('fetched_at'))} truncated={_int(hs.get('truncated'))} "
                f"history_error={_safe_error(hs.get('error') or hs.get('_error'))}"
            )


def main() -> int:
    started = time.monotonic()
    print(f"deployed_sha: {os.environ.get('BOOT_GIT_SHA','unknown')}")
    print("report_mode: LOW_MEMORY_SQLITE_ONLY")
    print("read_only: true")
    print("provider_calls: 0")
    with _readonly(DATA_DIR / "sibot.sqlite3") as conn:
        report_evm(conn)
    report_solana()
    print(f"\nreport_runtime_seconds: {time.monotonic() - started:.3f}")
    print("Threshold decision: DIAGNOSTIC ONLY — this report changes no leader-quality, LIVE, capital, wallet/signing or execution setting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
