#!/usr/bin/env python3
"""Read-only SiBot leader-gate and history-depth diagnostic.

Runs only in the restricted VPS snapshot. It compares the Top-20 ranking source with
reconstructed history used by the quality gate. It never changes thresholds/config,
never uses network credentials, and opens SQLite in query-only mode.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

if os.getenv("SIBOT_GATE_SNAPSHOT") != "1":
    print("Refusing to run outside the isolated SiBot gate snapshot.", file=sys.stderr)
    raise SystemExit(2)

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MAIN_PY = ROOT / "learnerbot" / "__main__.py"

from learnerbot.config import AppSettings, load_chains  # noqa: E402
from learnerbot import sibot as _sibot  # noqa: E402
from learnerbot import solana_sibot as _sol  # noqa: E402

STAGES = ["history_complete", "closed_trades", "historical_win_rate", "profit_factor", "drawdown", "recent_win_rate", "recent_profit_factor", "positive_net"]
IMPORT_RE = re.compile(r"^from \. import (\w+)")


def ro_db(path: Path) -> sqlite3.Connection:
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    conn = sqlite3.connect(f"file:{quote(p.as_posix(), safe='/')}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def blocked_write(*a, **k):
    raise RuntimeError("diagnostic blocked a configuration write")


def install_ro() -> None:
    _sibot.connect = lambda app: ro_db(_sibot.db_path(app))
    _sol.connect = lambda app: ro_db(_sol.db_path(app))
    _sibot.ensure_settings = lambda app: Path(app.csv_dir) / "sibot_settings.csv"
    _sol.ensure_settings = lambda app: Path(app.csv_dir) / "solana_settings.csv"
    _sibot._atomic_csv = blocked_write


def load_runtime_patches() -> None:
    for line in MAIN_PY.read_text(encoding="utf-8").splitlines():
        m = IMPORT_RE.match(line.strip())
        if not m:
            continue
        if m.group(1) == "cli":
            break
        __import__(f"learnerbot.{m.group(1)}")


install_ro()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    load_runtime_patches()
install_ro()
from learnerbot import sibot_profit_guard_patch as evm_guard  # noqa: E402
from learnerbot import solana_profit_guard_patch as sol_guard  # noqa: E402


def short(v) -> str:
    s = str(v or "")
    return s if len(s) <= 14 else f"{s[:7]}…{s[-5:]}"


def age(ts) -> str:
    try: n = int(ts or 0)
    except Exception: n = 0
    return "unknown" if not n else f"{max(0, time.time()-n)/3600:.1f}h"


def days(a, b) -> float:
    try: a, b = int(a or 0), int(b or 0)
    except Exception: return 0.0
    return max(0.0, (b-a)/86400) if a and b and b >= a else 0.0


def fail_evm(m, cfg):
    d = _sibot._dec
    tests = [
        ("history_complete", _sibot._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete")),
        ("closed_trades", int(m.get("closed") or 0) < _sibot._int(cfg.get("min_closed_trades"), 50)),
        ("historical_win_rate", d(m.get("win_rate")) < d(cfg.get("min_win_rate_pct"), 55)),
        ("profit_factor", d(m.get("profit_factor")) < d(cfg.get("min_profit_factor"), "1.5")),
        ("drawdown", d(m.get("drawdown_pct")) > d(cfg.get("max_leader_drawdown_pct"), 20)),
        ("recent_win_rate", d(m.get("recent_win_rate")) < d(cfg.get("min_recent_win_rate_pct"), 55)),
        ("recent_profit_factor", d(m.get("recent_profit_factor")) < d(cfg.get("min_recent_profit_factor"), "1.10")),
        ("positive_net", d(m.get("net")) <= 0),
    ]
    return next((name for name, failed in tests if failed), None)


def fail_sol(m, cfg):
    d = _sol._dec
    tests = [
        ("history_complete", _sol._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete")),
        ("closed_trades", int(m.get("closed") or 0) < max(1, _sol._int(cfg.get("min_closed_trades"), 10))),
        ("historical_win_rate", d(m.get("win_rate")) < d(cfg.get("min_win_rate_pct"), 65)),
        ("profit_factor", d(m.get("profit_factor")) < d(cfg.get("min_profit_factor"), "1.75")),
        ("drawdown", d(m.get("drawdown_pct")) > d(cfg.get("max_leader_drawdown_pct"), 20)),
        ("recent_win_rate", d(m.get("recent_win_rate")) < d(cfg.get("min_recent_win_rate_pct"), 65)),
        ("recent_profit_factor", d(m.get("recent_profit_factor")) < d(cfg.get("min_recent_profit_factor"), "1.50")),
        ("positive_net", d(m.get("net")) <= 0),
    ]
    return next((name for name, failed in tests if failed), None)


def funnel(label, entries):
    counts = {x: 0 for x in STAGES}; passed = 0
    for _, _, stage in entries:
        if stage is None: passed += 1
        else: counts[stage] += 1
    print(f"\n[{label}] Top-20 candidates: {len(entries)}")
    for stage in STAGES: print(f"  fail {stage}: {counts[stage]}")
    print(f"  qualified leaders: {passed}")


def evm_detail(app, chain_id, wallet, cutoff):
    with contextlib.closing(_sibot.connect(app)) as c:
        hs = c.execute("SELECT * FROM wallet_history_status WHERE chain_id=? AND lower(wallet)=?", (chain_id, wallet.lower())).fetchone()
        life = c.execute("SELECT COUNT(*) n FROM wallet_trades WHERE chain_id=? AND lower(wallet)=?", (chain_id, wallet.lower())).fetchone()
        win = c.execute("SELECT COUNT(*) n FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=?", (chain_id, wallet.lower(), cutoff)).fetchone()
    return dict(hs) if hs else {}, int(life["n"] or 0), int(win["n"] or 0)


def evm_reason(rank_closed, lifetime, window, hs, floor, lookback):
    if not hs: return "NO_HISTORY_STATUS"
    if str(hs.get("error") or "").strip(): return "HISTORY_ERROR"
    if rank_closed >= floor and window < floor: return "SOURCE_MISMATCH"
    if lifetime >= floor and window < floor: return "LOOKBACK_ACTIVITY"
    if days(hs.get("coverage_start_ts"), hs.get("coverage_end_ts")) + 1 < lookback: return "SHALLOW_COVERAGE"
    if lifetime < floor: return "LOW_RECONSTRUCTED_SAMPLE"
    return "BELOW_FLOOR"


def report_evm(app):
    print("=== EVM SiBot leader gate + history-depth report ===")
    users = [u for u in _sibot.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if not users: return
    tid = str(users[0].get("telegram_id") or "")
    for chain in load_chains(app, enabled_only=True):
        if str(chain.type).strip().lower() != "evm":
            continue
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        lookback = max(1, _sibot._int(cfg.get("lookback_days"), 60)); cutoff = int(time.time()) - lookback*86400
        floor = max(1, _sibot._int(cfg.get("min_closed_trades"), 50)); recent = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))
        rows = _sibot.ranking_rows(app, tid, chain.chain_id)
        entries = []
        for r in rows:
            m = evm_guard.quality_metrics(app, chain.chain_id, r.get("wallet"), lookback, recent)
            entries.append((r, m, fail_evm(m, cfg)))
        funnel(f"{chain.name} (account {tid})", entries)
        print("  settings: " + " ".join(f"{k}={cfg.get(k,'?')}" for k in ["lookback_days","min_closed_trades","require_complete_history","history_candidate_wallets","history_fetch_days","history_refresh_hours","history_max_pages","history_page_size"]))
        with contextlib.closing(_sibot.connect(app)) as c:
            hsagg = c.execute("SELECT COUNT(*) n,SUM(history_complete) complete,SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,MIN(fetched_at) oldest,MAX(fetched_at) newest FROM wallet_history_status WHERE chain_id=?", (chain.chain_id,)).fetchone()
            tagg = c.execute("SELECT COUNT(*) n,COUNT(DISTINCT lower(wallet)) wallets FROM wallet_trades WHERE chain_id=?", (chain.chain_id,)).fetchone()
        print(f"  history_store: status_wallets={int(hsagg['n'] or 0)} complete={int(hsagg['complete'] or 0)} errors={int(hsagg['errors'] or 0)} oldest_fetch_age={age(hsagg['oldest'])} newest_fetch_age={age(hsagg['newest'])} wallet_trades={int(tagg['n'] or 0)} wallets={int(tagg['wallets'] or 0)}")
        for r, m, stage in entries:
            wallet = str(r.get("wallet") or ""); hs, life, window = evm_detail(app, chain.chain_id, wallet, cutoff); rank_closed = int(r.get("closed_trades") or 0)
            reason = evm_reason(rank_closed, life, window, hs, floor, lookback)
            print(f"  candidate #{r.get('rank','?')} {short(wallet)} stage={stage or 'PASS'} ranking_closed={rank_closed} reconstructed_{lookback}d={int(m.get('closed') or 0)} lifetime_reconstructed={life} coverage={days(hs.get('coverage_start_ts'),hs.get('coverage_end_ts')):.1f}d fetch_age={age(hs.get('fetched_at'))} history_complete={bool(m.get('history_complete'))} unmatched_sells={int(hs.get('unmatched_sells') or 0)} rows={int(hs.get('normal_rows') or 0)}/{int(hs.get('token_rows') or 0)}/{int(hs.get('internal_rows') or 0)} diagnosis={reason}")


def report_solana(app):
    print("\n=== Solana SiBot leader gate + history-depth report ===")
    users = [u for u in _sol.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if not users: return
    tid = str(users[0].get("telegram_id") or ""); cfg = _sol.settings(app); rows = _sol.ranking_rows(app, tid)
    entries = []
    for r in rows:
        m = sol_guard.quality_metrics(app, r.get("wallet"), cfg); entries.append((r, m, fail_sol(m, cfg)))
    funnel(f"Solana (account {tid})", entries)
    print("  settings: " + " ".join(f"{k}={cfg.get(k,'?')}" for k in ["lookback_days","min_closed_trades","require_complete_history","candidate_limit","history_max_signatures","history_refresh_hours","rpc_delay_seconds","discovery_blocks_per_cycle","discovery_interval_seconds"]))
    with contextlib.closing(_sol.connect(app)) as c:
        agg = c.execute("SELECT COUNT(*) n,SUM(CASE WHEN truncated=0 AND COALESCE(error,'')='' THEN 1 ELSE 0 END) complete,SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,MIN(fetched_at) oldest,MAX(fetched_at) newest,SUM(signatures) signatures,SUM(swaps) swaps,SUM(closed_trades) closes FROM history_status").fetchone()
        ca = c.execute("SELECT COUNT(*) n,SUM(swap_events) events FROM candidates").fetchone()
    print(f"  history_store: candidates={int(ca['n'] or 0)} discovery_swap_events={int(ca['events'] or 0)} status_wallets={int(agg['n'] or 0)} complete={int(agg['complete'] or 0)} errors={int(agg['errors'] or 0)} oldest_fetch_age={age(agg['oldest'])} newest_fetch_age={age(agg['newest'])} signatures={int(agg['signatures'] or 0)} swaps={int(agg['swaps'] or 0)} closed_trades={int(agg['closes'] or 0)}")
    with contextlib.closing(_sol.connect(app)) as c:
        for r, m, stage in entries:
            wallet = str(r.get("wallet") or "")
            hs = c.execute("SELECT * FROM history_status WHERE wallet=?", (wallet,)).fetchone(); cand = c.execute("SELECT * FROM candidates WHERE wallet=?", (wallet,)).fetchone()
            hs = dict(hs) if hs else {}; cand = dict(cand) if cand else {}
            print(f"  candidate #{r.get('rank','?')} {short(wallet)} stage={stage or 'PASS'} ranking_closed={int(r.get('closed_trades') or 0)} reconstructed={int(m.get('closed') or 0)} status_closed={int(hs.get('closed_trades') or 0)} coverage={days(hs.get('coverage_start_ts'),hs.get('coverage_end_ts')):.1f}d fetch_age={age(hs.get('fetched_at'))} complete={bool(m.get('history_complete'))} signatures={int(hs.get('signatures') or 0)} swaps={int(hs.get('swaps') or 0)} truncated={int(hs.get('truncated') or 0)} discovery_events={int(cand.get('swap_events') or 0)} last_seen_age={age(cand.get('last_seen'))}")


def main() -> int:
    app = AppSettings.load(); report_evm(app); report_solana(app); return 0


if __name__ == "__main__":
    raise SystemExit(main())
