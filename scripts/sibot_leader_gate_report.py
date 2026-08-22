#!/usr/bin/env python3
"""Read-only SiBot leader-quality gate and history-depth diagnostic."""
from __future__ import annotations

import contextlib
import io
import os
import re
import sqlite3
import sys
import time
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

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MAIN_PY = ROOT / "learnerbot" / "__main__.py"

from learnerbot.config import AppSettings, load_chains  # noqa: E402
from learnerbot import sibot as _sibot  # noqa: E402
from learnerbot import solana_sibot as _sol  # noqa: E402

_IMPORT_RE = re.compile(r"^from \. import (\w+)")
FUNNEL_STAGES = [
    "history_complete",
    "closed_trades",
    "historical_win_rate",
    "profit_factor",
    "drawdown",
    "recent_win_rate",
    "recent_profit_factor",
    "positive_net",
]


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"required database is missing: {p}")
    conn = sqlite3.connect(f"file:{quote(p.as_posix(), safe='/')}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _blocked_config_write(*_args, **_kwargs):
    raise RuntimeError("SiBot leader-gate report blocked a configuration write")


def _install_readonly_guards() -> None:
    def sibot_settings_path(app):
        return Path(app.csv_dir) / "sibot_settings.csv"

    def solana_settings_path(app):
        return Path(app.csv_dir) / "solana_settings.csv"

    _sibot.connect = lambda app: _readonly_sqlite(_sibot.db_path(app))
    _sol.connect = lambda app: _readonly_sqlite(_sol.db_path(app))
    _sibot.ensure_settings = sibot_settings_path
    _sol.ensure_settings = solana_settings_path
    _sibot._atomic_csv = _blocked_config_write


def _load_patch_chain() -> None:
    for line in MAIN_PY.read_text(encoding="utf-8").splitlines():
        match = _IMPORT_RE.match(line.strip())
        if not match:
            continue
        module = match.group(1)
        if module == "cli":
            break
        __import__(f"learnerbot.{module}")


_install_readonly_guards()
_patch_import_output = io.StringIO()
with contextlib.redirect_stdout(_patch_import_output), contextlib.redirect_stderr(_patch_import_output):
    _load_patch_chain()
_install_readonly_guards()

from learnerbot import sibot_profit_guard_patch as _evm_guard  # noqa: E402
from learnerbot import solana_profit_guard_patch as _sol_guard  # noqa: E402


def _int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _short(value: object) -> str:
    text = str(value or "")
    return text if len(text) <= 14 else f"{text[:7]}…{text[-5:]}"


def _age_hours(ts: object) -> str:
    value = _int(ts, 0)
    return "unknown" if not value else f"{max(0.0, time.time()-value)/3600.0:.1f}h"


def _iso(ts: object) -> str:
    value = _int(ts, 0)
    if not value:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage_days(start: object, end: object) -> float:
    a, b = _int(start, 0), _int(end, 0)
    return max(0.0, (b-a)/86400.0) if a and b and b >= a else 0.0


def _safe_one(conn: sqlite3.Connection, sql: str, params=()) -> dict:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _evm_stage_failed(m: dict, cfg: dict) -> str | None:
    d = _sibot._dec
    if _sibot._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return "history_complete"
    if _int(m.get("closed")) < max(1, _sibot._int(cfg.get("min_closed_trades"), 50)):
        return "closed_trades"
    if d(m.get("win_rate")) < d(cfg.get("min_win_rate_pct"), 55):
        return "historical_win_rate"
    if d(m.get("profit_factor")) < d(cfg.get("min_profit_factor"), "1.5"):
        return "profit_factor"
    if d(m.get("drawdown_pct")) > d(cfg.get("max_leader_drawdown_pct"), 20):
        return "drawdown"
    if d(m.get("recent_win_rate")) < d(cfg.get("min_recent_win_rate_pct"), 55):
        return "recent_win_rate"
    if d(m.get("recent_profit_factor")) < d(cfg.get("min_recent_profit_factor"), "1.10"):
        return "recent_profit_factor"
    if d(m.get("net")) <= 0:
        return "positive_net"
    return None


def _sol_stage_failed(m: dict, cfg: dict) -> str | None:
    d = _sol._dec
    if _sol._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return "history_complete"
    if _int(m.get("closed")) < max(1, _sol._int(cfg.get("min_closed_trades"), 10)):
        return "closed_trades"
    if d(m.get("win_rate")) < d(cfg.get("min_win_rate_pct"), 65):
        return "historical_win_rate"
    if d(m.get("profit_factor")) < d(cfg.get("min_profit_factor"), "1.75"):
        return "profit_factor"
    if d(m.get("drawdown_pct")) > d(cfg.get("max_leader_drawdown_pct"), 20):
        return "drawdown"
    if d(m.get("recent_win_rate")) < d(cfg.get("min_recent_win_rate_pct"), 65):
        return "recent_win_rate"
    if d(m.get("recent_profit_factor")) < d(cfg.get("min_recent_profit_factor"), "1.50"):
        return "recent_profit_factor"
    if d(m.get("net")) <= 0:
        return "positive_net"
    return None


def _funnel(label: str, rows: list[tuple[dict, dict, str | None]]) -> None:
    counts = {stage: 0 for stage in FUNNEL_STAGES}
    passed = 0
    for _, _, stage in rows:
        if stage is None:
            passed += 1
        else:
            counts[stage] += 1
    print(f"\n[{label}] Top-20 candidates: {len(rows)}")
    for stage in FUNNEL_STAGES:
        print(f"  fail {stage}: {counts[stage]}")
    print(f"  qualified leaders: {passed}")


def _evm_history_detail(app, chain_id: int, wallet: str, lookback_days: int) -> dict:
    cutoff = int(time.time()) - max(1, int(lookback_days)) * 86400
    with contextlib.closing(_sibot.connect(app)) as conn:
        hs = _safe_one(
            conn,
            """SELECT fetched_at,coverage_start_ts,coverage_end_ts,history_complete,
                      unmatched_sells,normal_rows,token_rows,internal_rows,error
               FROM wallet_history_status WHERE chain_id=? AND lower(wallet)=?""",
            (int(chain_id), str(wallet).lower()),
        )
        lifetime = _safe_one(
            conn,
            """SELECT COUNT(*) AS n,MIN(sell_ts) AS first_close,MAX(sell_ts) AS last_close
               FROM wallet_trades WHERE chain_id=? AND lower(wallet)=?""",
            (int(chain_id), str(wallet).lower()),
        )
        window = _safe_one(
            conn,
            """SELECT COUNT(*) AS n,MIN(sell_ts) AS first_close,MAX(sell_ts) AS last_close
               FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=?""",
            (int(chain_id), str(wallet).lower(), cutoff),
        )
    return {"history": hs, "lifetime": lifetime, "window": window}


def _evm_diagnosis(ranking_closed: int, detail: dict, minimum: int, lookback_days: int) -> str:
    hs, lifetime, window = detail["history"], detail["lifetime"], detail["window"]
    if hs.get("_error"):
        return "HISTORY_STATUS_QUERY_ERROR"
    if not hs:
        return "NO_HISTORY_STATUS"
    if str(hs.get("error") or "").strip():
        return "HISTORY_ERROR"
    life_n, win_n = _int(lifetime.get("n")), _int(window.get("n"))
    if ranking_closed >= minimum and win_n < minimum:
        return "SOURCE_MISMATCH"
    if life_n >= minimum and win_n < minimum:
        return "LOOKBACK_ACTIVITY"
    if _coverage_days(hs.get("coverage_start_ts"), hs.get("coverage_end_ts")) + 1.0 < float(lookback_days):
        return "SHALLOW_COVERAGE"
    if life_n < minimum:
        return "LOW_RECONSTRUCTED_SAMPLE"
    return "BELOW_FLOOR"


def report_evm(app) -> list[tuple[str, object]]:
    print("=== EVM SiBot leader gate + history-depth report ===")
    depth: list[tuple[str, object]] = []
    users = [u for u in _sibot.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    for chain in load_chains(app, enabled_only=True):
        if str(chain.type).strip().lower() != "evm":
            continue
        if not users:
            print(f"\n[{chain.name}] no enabled account on this chain -- skipped")
            continue
        tid = str(users[0].get("telegram_id") or "")
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        lookback = max(1, _sibot._int(cfg.get("lookback_days"), 60))
        minimum = max(1, _sibot._int(cfg.get("min_closed_trades"), 50))
        recent_n = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))
        candidates = _sibot.ranking_rows(app, tid, chain.chain_id)
        rows = []
        for ranking in candidates:
            metrics = _evm_guard.quality_metrics(app, chain.chain_id, ranking.get("wallet"), lookback, recent_n)
            rows.append((ranking, metrics, _evm_stage_failed(metrics, cfg)))
        _funnel(f"{chain.name} (account {tid})", rows)
        depth.append((chain.name, cfg.get("history_candidate_wallets")))
        print(
            "  effective history settings: "
            + " ".join(
                f"{key}={cfg.get(key, '<missing>')}" for key in [
                    "lookback_days", "min_closed_trades", "require_complete_history",
                    "history_candidate_wallets", "history_fetch_days", "history_refresh_hours",
                    "history_max_pages", "history_page_size", "history_worker_seconds",
                ]
            )
        )
        with contextlib.closing(_sibot.connect(app)) as conn:
            status = _safe_one(
                conn,
                """SELECT COUNT(*) AS n,SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) AS complete,
                          SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) AS errors,
                          MIN(fetched_at) AS oldest,MAX(fetched_at) AS newest,
                          MIN(coverage_start_ts) AS coverage_start,MAX(coverage_end_ts) AS coverage_end
                   FROM wallet_history_status WHERE chain_id=?""",
                (int(chain.chain_id),),
            )
            trades = _safe_one(
                conn,
                """SELECT COUNT(*) AS n,COUNT(DISTINCT lower(wallet)) AS wallets,
                          MIN(sell_ts) AS first_close,MAX(sell_ts) AS last_close
                   FROM wallet_trades WHERE chain_id=?""",
                (int(chain.chain_id),),
            )
        print(
            "  history store: "
            f"status_wallets={_int(status.get('n'))} complete={_int(status.get('complete'))} errors={_int(status.get('errors'))} "
            f"oldest_fetch={_iso(status.get('oldest'))} newest_fetch={_iso(status.get('newest'))} "
            f"coverage={_coverage_days(status.get('coverage_start'), status.get('coverage_end')):.1f}d "
            f"wallet_trades={_int(trades.get('n'))} wallets_with_trades={_int(trades.get('wallets'))} "
            f"first_reconstructed_close={_iso(trades.get('first_close'))} last_reconstructed_close={_iso(trades.get('last_close'))}"
        )
        print("  worker health retention: no EVM history-worker heartbeat/error marker exists in current reliability patch; use history_status/trade timestamps as evidence")
        for ranking, metrics, stage in rows:
            wallet = str(ranking.get("wallet") or "")
            detail = _evm_history_detail(app, chain.chain_id, wallet, lookback)
            hs, life, win = detail["history"], detail["lifetime"], detail["window"]
            ranking_closed = _int(ranking.get("closed_trades"))
            print(
                f"  candidate #{ranking.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
                f"ranking_closed={ranking_closed} reconstructed_{lookback}d={_int(metrics.get('closed'))} "
                f"lifetime_reconstructed={_int(life.get('n'))} first_close={_iso(life.get('first_close'))} last_close={_iso(life.get('last_close'))} "
                f"coverage={_coverage_days(hs.get('coverage_start_ts'), hs.get('coverage_end_ts')):.1f}d "
                f"fetch={_iso(hs.get('fetched_at'))} history_complete={bool(metrics.get('history_complete'))} "
                f"unmatched_sells={_int(hs.get('unmatched_sells'))} "
                f"rows(normal/token/internal)={_int(hs.get('normal_rows'))}/{_int(hs.get('token_rows'))}/{_int(hs.get('internal_rows'))} "
                f"diagnosis={_evm_diagnosis(ranking_closed, detail, minimum, lookback)}"
            )
    return depth


def report_solana(app) -> object:
    print("\n=== Solana SiBot leader gate + history-depth report ===")
    users = [u for u in _sol.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if not users:
        print("no enabled Solana account -- skipped")
        return None
    tid = str(users[0].get("telegram_id") or "")
    cfg = _sol.settings(app)
    candidates = _sol.ranking_rows(app, tid)
    rows = []
    for ranking in candidates:
        metrics = _sol_guard.quality_metrics(app, ranking.get("wallet"), cfg)
        rows.append((ranking, metrics, _sol_stage_failed(metrics, cfg)))
    _funnel(f"Solana (account {tid})", rows)
    print(
        "  effective history settings: "
        + " ".join(
            f"{key}={cfg.get(key, '<missing>')}" for key in [
                "lookback_days", "min_closed_trades", "require_complete_history", "candidate_limit",
                "history_max_signatures", "history_refresh_hours", "history_worker_seconds",
                "rpc_delay_seconds", "discovery_blocks_per_cycle", "discovery_interval_seconds",
            ]
        )
    )
    with contextlib.closing(_sol.connect(app)) as conn:
        status = _safe_one(
            conn,
            """SELECT COUNT(*) AS n,SUM(CASE WHEN truncated=0 AND COALESCE(error,'')='' THEN 1 ELSE 0 END) AS complete,
                      SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) AS errors,
                      MIN(fetched_at) AS oldest,MAX(fetched_at) AS newest,
                      MIN(coverage_start_ts) AS coverage_start,MAX(coverage_end_ts) AS coverage_end,
                      SUM(signatures) AS signatures,SUM(swaps) AS swaps,SUM(closed_trades) AS closes
               FROM history_status""",
        )
        discovered = _safe_one(conn, "SELECT COUNT(*) AS n,SUM(swap_events) AS events,MIN(first_seen) AS first_seen,MAX(last_seen) AS last_seen FROM candidates")
        worker_last_run = _sol._state(conn, "worker:history:last_run", 0)
        worker_last_success = _sol._state(conn, "worker:history:last_success", 0)
        worker_last_error = _sol._state(conn, "worker:history:last_error", "")
    print(
        "  history store: "
        f"candidates={_int(discovered.get('n'))} discovery_swap_events={_int(discovered.get('events'))} "
        f"first_candidate_seen={_iso(discovered.get('first_seen'))} last_candidate_seen={_iso(discovered.get('last_seen'))} "
        f"status_wallets={_int(status.get('n'))} complete={_int(status.get('complete'))} errors={_int(status.get('errors'))} "
        f"oldest_fetch={_iso(status.get('oldest'))} newest_fetch={_iso(status.get('newest'))} "
        f"coverage={_coverage_days(status.get('coverage_start'), status.get('coverage_end')):.1f}d "
        f"signatures={_int(status.get('signatures'))} swaps={_int(status.get('swaps'))} closed_trades={_int(status.get('closes'))}"
    )
    print(
        "  history worker marker: "
        f"last_run={_iso(worker_last_run)} last_success={_iso(worker_last_success)} "
        f"last_error={str(worker_last_error or '')[:240] or '<none>'} retention=LATEST_ONLY"
    )
    print("  Aug-18 streak proof: unavailable from worker marker alone because the state table overwrites last_run/last_success/last_error rather than retaining a time series")
    with contextlib.closing(_sol.connect(app)) as conn:
        for ranking, metrics, stage in rows:
            wallet = str(ranking.get("wallet") or "")
            hs = _safe_one(
                conn,
                """SELECT fetched_at,coverage_start_ts,coverage_end_ts,signatures,swaps,closed_trades,truncated,error
                   FROM history_status WHERE wallet=?""",
                (wallet,),
            )
            cand = _safe_one(conn, "SELECT first_seen,last_seen,swap_events,updated_at FROM candidates WHERE wallet=?", (wallet,))
            print(
                f"  candidate #{ranking.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
                f"ranking_closed={_int(ranking.get('closed_trades'))} reconstructed={_int(metrics.get('closed'))} status_closed={_int(hs.get('closed_trades'))} "
                f"coverage={_coverage_days(hs.get('coverage_start_ts'), hs.get('coverage_end_ts')):.1f}d fetch={_iso(hs.get('fetched_at'))} "
                f"complete={bool(metrics.get('history_complete'))} signatures={_int(hs.get('signatures'))} swaps={_int(hs.get('swaps'))} "
                f"truncated={_int(hs.get('truncated'))} history_error={str(hs.get('error') or '')[:120] or '<none>'} "
                f"discovery_events={_int(cand.get('swap_events'))} first_seen={_iso(cand.get('first_seen'))} last_seen={_iso(cand.get('last_seen'))}"
            )
    return cfg.get("candidate_limit")


def main() -> int:
    app = AppSettings.load()
    evm_depth = report_evm(app)
    sol_depth = report_solana(app)
    print("\n=== Discovery/history depth comparison ===")
    for chain_name, value in evm_depth:
        print(f"  EVM {chain_name}: history_candidate_wallets={value}")
    print(f"  Solana: candidate_limit={sol_depth}")
    print("  Threshold decision: DIAGNOSTIC ONLY — this report changes no leader-quality, LIVE, capital, wallet/signing or execution setting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
