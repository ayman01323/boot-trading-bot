#!/usr/bin/env python3
"""Read-only per-chain SiBot leader-quality gate and history-depth report.

This diagnostic intentionally refuses to run against the live repository tree.
The restricted VPS wrapper creates a temporary snapshot containing tracked
runtime code, a point-in-time copy of the live CSV configuration, and
consistent backups of the SiBot SQLite databases. Only that snapshot is
analysed.
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


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"required database is missing: {p}")
    uri = f"file:{quote(p.as_posix(), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
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
    text = MAIN_PY.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        match = _IMPORT_RE.match(line)
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


def _age_hours(ts: object) -> str:
    try:
        value = int(ts or 0)
    except Exception:
        value = 0
    if not value:
        return "unknown"
    return f"{max(0.0, time.time() - value) / 3600.0:.1f}h"


def _coverage_days(start: object, end: object) -> float:
    try:
        a, b = int(start or 0), int(end or 0)
    except Exception:
        return 0.0
    if not a or not b or b < a:
        return 0.0
    return (b - a) / 86400.0


def _short(wallet: object) -> str:
    value = str(wallet or "")
    return value if len(value) <= 14 else f"{value[:7]}…{value[-5:]}"


def _evm_stage_failed(m: dict, cfg: dict) -> str | None:
    d = _sibot._dec
    if _sibot._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return "history_complete"
    if int(m.get("closed") or 0) < int(_sibot._int(cfg.get("min_closed_trades"), 50)):
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
    if int(m.get("closed") or 0) < max(1, _sol._int(cfg.get("min_closed_trades"), 10)):
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


def _print_funnel(label: str, candidates: int, counts: dict, qualified: int) -> None:
    print(f"\n[{label}] Top-20 candidates: {candidates}")
    for stage in FUNNEL_STAGES:
        print(f"  fail {stage}: {counts[stage]}")
    print(f"  qualified leaders: {qualified}")


def _evm_history_detail(app, chain_id: int, wallet: str, lookback_days: int) -> dict:
    cutoff = int(time.time()) - max(1, int(lookback_days)) * 86400
    with contextlib.closing(_sibot.connect(app)) as conn:
        hs = conn.execute(
            """SELECT fetched_at,coverage_start_ts,coverage_end_ts,history_complete,
                      unmatched_sells,normal_rows,token_rows,internal_rows,error
               FROM wallet_history_status
               WHERE chain_id=? AND lower(wallet)=?""",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
        lifetime = conn.execute(
            "SELECT COUNT(*) AS n FROM wallet_trades WHERE chain_id=? AND lower(wallet)=?",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
        window = conn.execute(
            "SELECT COUNT(*) AS n FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=?",
            (int(chain_id), str(wallet).lower(), cutoff),
        ).fetchone()
    return {
        "history": dict(hs) if hs else {},
        "lifetime_closed": int(lifetime["n"] or 0) if lifetime else 0,
        "lookback_closed": int(window["n"] or 0) if window else 0,
    }


def _evm_diagnosis(ranking_closed: int, detail: dict, minimum: int, lookback_days: int) -> str:
    hs = detail.get("history") or {}
    lifetime = int(detail.get("lifetime_closed") or 0)
    window = int(detail.get("lookback_closed") or 0)
    if not hs:
        return "NO_HISTORY_STATUS"
    if str(hs.get("error") or "").strip():
        return "HISTORY_ERROR"
    if ranking_closed >= minimum and window < minimum:
        return "SOURCE_MISMATCH"
    if lifetime >= minimum and window < minimum:
        return "LOOKBACK_ACTIVITY"
    coverage = _coverage_days(hs.get("coverage_start_ts"), hs.get("coverage_end_ts"))
    if coverage + 1.0 < float(lookback_days):
        return "SHALLOW_COVERAGE"
    if lifetime < minimum:
        return "LOW_RECONSTRUCTED_SAMPLE"
    return "BELOW_FLOOR"


def report_evm(app) -> None:
    print("=== EVM SiBot leader gate + history-depth report ===")
    for chain in load_chains(app, enabled_only=True):
        if str(chain.type).strip().lower() != "evm":
            continue
        users = [
            u for u in _sibot.all_users(app.csv_dir, enabled_only=True)
            if str(u.get("status") or "").upper() == "ACTIVE"
        ]
        if not users:
            print(f"\n[{chain.name}] no enabled account on this chain -- skipped")
            continue
        tid = str(users[0].get("telegram_id") or "")
        try:
            candidates = _sibot.ranking_rows(app, tid, chain.chain_id)
        except Exception as exc:
            print(f"\n[{chain.name}] ERROR reading rankings: {exc}")
            continue
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        lookback = max(1, _sibot._int(cfg.get("lookback_days"), 60))
        minimum = max(1, _sibot._int(cfg.get("min_closed_trades"), 50))
        recent_n = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))
        counts = {s: 0 for s in FUNNEL_STAGES}
        qualified = 0
        rows: list[tuple[dict, dict, str | None]] = []
        for r in candidates:
            m = _evm_guard.quality_metrics(app, chain.chain_id, r.get("wallet"), lookback, recent_n)
            stage = _evm_stage_failed(m, cfg)
            if stage is None:
                qualified += 1
            else:
                counts[stage] += 1
            rows.append((r, m, stage))
        _print_funnel(f"{chain.name} (account {tid})", len(candidates), counts, qualified)
        print(
            "  effective history settings: "
            + " ".join(
                f"{key}={cfg.get(key, '<missing>')}"
                for key in [
                    "lookback_days", "min_closed_trades", "require_complete_history",
                    "history_candidate_wallets", "history_fetch_days", "history_refresh_hours",
                    "history_max_pages", "history_page_size", "history_worker_seconds",
                ]
            )
        )
        with contextlib.closing(_sibot.connect(app)) as conn:
            status = conn.execute(
                """SELECT COUNT(*) AS n,
                          SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) AS complete,
                          SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) AS errors,
                          MIN(fetched_at) AS oldest,MAX(fetched_at) AS newest
                   FROM wallet_history_status WHERE chain_id=?""",
                (int(chain.chain_id),),
            ).fetchone()
            trades = conn.execute(
                "SELECT COUNT(*) AS n,COUNT(DISTINCT lower(wallet)) AS wallets FROM wallet_trades WHERE chain_id=?",
                (int(chain.chain_id),),
            ).fetchone()
        print(
            "  history store: "
            f"status_wallets={int(status['n'] or 0)} complete={int(status['complete'] or 0)} "
            f"errors={int(status['errors'] or 0)} oldest_fetch_age={_age_hours(status['oldest'])} "
            f"newest_fetch_age={_age_hours(status['newest'])} wallet_trades={int(trades['n'] or 0)} "
            f"wallets_with_trades={int(trades['wallets'] or 0)}"
        )
        for r, m, stage in rows:
            wallet = str(r.get("wallet") or "")
            detail = _evm_history_detail(app, chain.chain_id, wallet, lookback)
            hs = detail["history"]
            ranking_closed = int(r.get("closed_trades") or 0)
            diagnosis = _evm_diagnosis(ranking_closed, detail, minimum, lookback)
            print(
                f"  candidate #{r.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
                f"ranking_closed={ranking_closed} reconstructed_{lookback}d={int(m.get('closed') or 0)} "
                f"lifetime_reconstructed={detail['lifetime_closed']} "
                f"coverage={_coverage_days(hs.get('coverage_start_ts'), hs.get('coverage_end_ts')):.1f}d "
                f"fetch_age={_age_hours(hs.get('fetched_at'))} history_complete={bool(m.get('history_complete'))} "
                f"unmatched_sells={int(hs.get('unmatched_sells') or 0)} "
                f"rows(normal/token/internal)={int(hs.get('normal_rows') or 0)}/{int(hs.get('token_rows') or 0)}/{int(hs.get('internal_rows') or 0)} "
                f"diagnosis={diagnosis}"
            )


def report_solana(app) -> None:
    print("\n=== Solana SiBot leader gate + history-depth report ===")
    users = [
        u for u in _sol.all_users(app.csv_dir, enabled_only=True)
        if str(u.get("status") or "").upper() == "ACTIVE"
    ]
    if not users:
        print("no enabled Solana account -- skipped")
        return
    tid = str(users[0].get("telegram_id") or "")
    cfg = _sol.settings(app)
    try:
        candidates = _sol.ranking_rows(app, tid)
    except Exception as exc:
        print(f"ERROR reading Solana rankings: {exc}")
        return
    counts = {s: 0 for s in FUNNEL_STAGES}
    qualified = 0
    rows: list[tuple[dict, dict, str | None]] = []
    for r in candidates:
        wallet = r.get("wallet")
        m = _sol_guard.quality_metrics(app, wallet, cfg)
        stage = _sol_stage_failed(m, cfg)
        if stage is None:
            qualified += 1
        else:
            counts[stage] += 1
        rows.append((r, m, stage))
    _print_funnel(f"Solana (account {tid})", len(candidates), counts, qualified)
    print(
        "  effective history settings: "
        + " ".join(
            f"{key}={cfg.get(key, '<missing>')}"
            for key in [
                "lookback_days", "min_closed_trades", "require_complete_history",
                "candidate_limit", "history_max_signatures", "history_refresh_hours",
                "rpc_delay_seconds", "discovery_blocks_per_cycle", "discovery_interval_seconds",
            ]
        )
    )
    with contextlib.closing(_sol.connect(app)) as conn:
        status = conn.execute(
            """SELECT COUNT(*) AS n,
                      SUM(CASE WHEN truncated=0 AND COALESCE(error,'')='' THEN 1 ELSE 0 END) AS complete,
                      SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) AS errors,
                      MIN(fetched_at) AS oldest,MAX(fetched_at) AS newest,
                      SUM(signatures) AS signatures,SUM(swaps) AS swaps,SUM(closed_trades) AS closes
               FROM history_status"""
        ).fetchone()
        discovered = conn.execute(
            "SELECT COUNT(*) AS n,SUM(swap_events) AS events FROM candidates"
        ).fetchone()
    print(
        "  history store: "
        f"candidates={int(discovered['n'] or 0)} discovery_swap_events={int(discovered['events'] or 0)} "
        f"status_wallets={int(status['n'] or 0)} complete={int(status['complete'] or 0)} "
        f"errors={int(status['errors'] or 0)} oldest_fetch_age={_age_hours(status['oldest'])} "
        f"newest_fetch_age={_age_hours(status['newest'])} signatures={int(status['signatures'] or 0)} "
        f"swaps={int(status['swaps'] or 0)} closed_trades={int(status['closes'] or 0)}"
    )
    with contextlib.closing(_sol.connect(app)) as conn:
        for r, m, stage in rows:
            wallet = str(r.get("wallet") or "")
            hs = conn.execute(
                """SELECT fetched_at,coverage_start_ts,coverage_end_ts,signatures,swaps,
                          closed_trades,truncated,error FROM history_status WHERE wallet=?""",
                (wallet,),
            ).fetchone()
            cand = conn.execute(
                "SELECT first_seen,last_seen,swap_events,updated_at FROM candidates WHERE wallet=?",
                (wallet,),
            ).fetchone()
            hs = dict(hs) if hs else {}
            cand = dict(cand) if cand else {}
            print(
                f"  candidate #{r.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
                f"ranking_closed={int(r.get('closed_trades') or 0)} reconstructed={int(m.get('closed') or 0)} "
                f"status_closed={int(hs.get('closed_trades') or 0)} "
                f"coverage={_coverage_days(hs.get('coverage_start_ts'), hs.get('coverage_end_ts')):.1f}d "
                f"fetch_age={_age_hours(hs.get('fetched_at'))} complete={bool(m.get('history_complete'))} "
                f"signatures={int(hs.get('signatures') or 0)} swaps={int(hs.get('swaps') or 0)} "
                f"truncated={int(hs.get('truncated') or 0)} discovery_events={int(cand.get('swap_events') or 0)} "
                f"last_seen_age={_age_hours(cand.get('last_seen'))}"
            )


def main() -> int:
    app = AppSettings.load()
    report_evm(app)
    report_solana(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
