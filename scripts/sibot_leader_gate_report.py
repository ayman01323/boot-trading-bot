#!/usr/bin/env python3
"""Read-only per-chain SiBot leader-quality and history-depth diagnostic.

The restricted VPS wrapper snapshots tracked runtime code, live CSV configuration,
and consistent SQLite backups. This script refuses to run outside that isolated
snapshot and never performs network calls or configuration/database writes.
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


def _short(wallet: object) -> str:
    value = str(wallet or "")
    return value if len(value) <= 14 else f"{value[:7]}…{value[-5:]}"


def _age_hours(ts: object) -> str:
    try:
        n = int(ts or 0)
    except Exception:
        n = 0
    if not n:
        return "unknown"
    return f"{max(0, time.time() - n) / 3600:.1f}h"


def _coverage_days(start: object, end: object) -> float:
    try:
        a, b = int(start or 0), int(end or 0)
    except Exception:
        return 0.0
    return max(0.0, (b - a) / 86400) if a and b and b >= a else 0.0


def _print_settings(title: str, cfg: dict, keys: list[str]) -> None:
    print(f"  {title}:")
    for key in keys:
        print(f"    {key}={cfg.get(key, '<missing>')}")


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


def _evm_candidate_detail(app, chain_id: int, wallet: str, lookback_days: int) -> dict:
    cutoff = int(time.time()) - max(1, int(lookback_days)) * 86400
    with contextlib.closing(_sibot.connect(app)) as conn:
        hs = conn.execute(
            """SELECT fetched_at,coverage_start_ts,coverage_end_ts,history_complete,unmatched_sells,
                      normal_rows,token_rows,internal_rows,error
               FROM wallet_history_status WHERE chain_id=? AND lower(wallet)=?""",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
        life = conn.execute(
            """SELECT COUNT(*) n,MIN(sell_ts) first_sell,MAX(sell_ts) last_sell
               FROM wallet_trades WHERE chain_id=? AND lower(wallet)=?""",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
        win = conn.execute(
            """SELECT COUNT(*) n,MIN(sell_ts) first_sell,MAX(sell_ts) last_sell
               FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=?""",
            (int(chain_id), str(wallet).lower(), cutoff),
        ).fetchone()
    return {
        "history": dict(hs) if hs else {},
        "lifetime_closed": int(life["n"] or 0) if life else 0,
        "lookback_closed": int(win["n"] or 0) if win else 0,
        "first_sell": int(life["first_sell"] or 0) if life else 0,
        "last_sell": int(life["last_sell"] or 0) if life else 0,
    }


def _evm_diagnosis(ranking_closed: int, detail: dict, minimum: int, lookback_days: int) -> str:
    hs = detail.get("history") or {}
    lookback_closed = int(detail.get("lookback_closed") or 0)
    lifetime = int(detail.get("lifetime_closed") or 0)
    if not hs:
        return "NO_HISTORY_STATUS: candidate has not completed a recorded EVM history refresh"
    if str(hs.get("error") or "").strip():
        return "HISTORY_ERROR: reconstruction recorded an error"
    coverage = _coverage_days(hs.get("coverage_start_ts"), hs.get("coverage_end_ts"))
    if ranking_closed >= minimum and lookback_closed < minimum:
        return "SOURCE_MISMATCH: Top-20 evidence has enough results but wallet_trades reconstruction does not"
    if lifetime >= minimum and lookback_closed < minimum:
        return "LOOKBACK_ACTIVITY: enough lifetime closes exist, but fewer than the floor are inside the current lookback"
    if coverage and coverage + 1 < lookback_days:
        return "SHALLOW_COVERAGE: reconstructed history span is shorter than the configured lookback"
    if lifetime < minimum:
        return "LOW_RECONSTRUCTED_SAMPLE: fewer than the floor exist in reconstructed lifetime history"
    return "BELOW_FLOOR: inspect reconstruction/source alignment"


def report_evm(app) -> None:
    print("=== EVM SiBot leader gate + history-depth report ===")
    for chain in load_chains(app, enabled_only=True):
        if str(chain.type).strip().lower() != "evm":
            continue
        users = [u for u in _sibot.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
        if not users:
            print(f"\n[{chain.name}] no enabled account -- skipped")
            continue
        tid = str(users[0].get("telegram_id") or "")
        candidates = _sibot.ranking_rows(app, tid, chain.chain_id)
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        lookback = max(1, _sibot._int(cfg.get("lookback_days"), 60))
        minimum = max(1, _sibot._int(cfg.get("min_closed_trades"), 50))
        recent_n = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))
        counts = {s: 0 for s in FUNNEL_STAGES}
        qualified = 0
        metrics: list[tuple[dict, dict, str | None]] = []
        for r in candidates:
            m = _evm_guard.quality_metrics(app, chain.chain_id, r.get("wallet"), lookback, recent_n)
            stage = _evm_stage_failed(m, cfg)
            counts[stage] += 1 if stage else 0
            qualified += 1 if stage is None else 0
            metrics.append((r, m, stage))
        _print_funnel(f"{chain.name} (account {tid})", len(candidates), counts, qualified)
        _print_settings(
            "effective history settings",
            cfg,
            ["lookback_days", "min_closed_trades", "require_complete_history", "history_candidate_wallets",
             "history_fetch_days", "history_refresh_hours", "history_max_pages", "history_page_size", "history_worker_seconds"],
        )
        with contextlib.closing(_sibot.connect(app)) as conn:
            hs = conn.execute(
                """SELECT COUNT(*) n,SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) complete,
                          SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,
                          MIN(fetched_at) oldest,MAX(fetched_at) newest
                   FROM wallet_history_status WHERE chain_id=?""",
                (int(chain.chain_id),),
            ).fetchone()
            wt = conn.execute(
                "SELECT COUNT(*) n,COUNT(DISTINCT lower(wallet)) wallets FROM wallet_trades WHERE chain_id=?",
                (int(chain.chain_id),),
            ).fetchone()
        print(
            "  history store: "
            f"status_wallets={int(hs['n'] or 0)} complete={int(hs['complete'] or 0)} errors={int(hs['errors'] or 0)} "
            f"oldest_fetch_age={_age_hours(hs['oldest'])} newest_fetch_age={_age_hours(hs['newest'])} "
            f"wallet_trades={int(wt['n'] or 0)} wallets_with_trades={int(wt['wallets'] or 0)}"
        )
        print("  candidate detail (rank source vs reconstructed quality source):")
        for r, m, stage in metrics:
            wallet = str(r.get("wallet") or "")
            detail = _evm_candidate_detail(app, chain.chain_id, wallet, lookback)
            hsrow = detail.get("history") or {}
            ranking_closed = int(r.get("closed_trades") or 0)
            diagnosis = _evm_diagnosis(ranking_closed, detail, minimum, lookback)
            print(
                f"    #{r.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
                f"ranking_closed={ranking_closed} reconstructed_{lookback}d={int(m.get('closed') or 0)} "
                f"lifetime_reconstructed={detail['lifetime_closed']} history_complete={bool(m.get('history_complete'))} "
                f"coverage={_coverage_days(hsrow.get('coverage_start_ts'), hsrow.get('coverage_end_ts')):.1f}d "
                f"fetch_age={_age_hours(hsrow.get('fetched_at'))} unmatched_sells={int(hsrow.get('unmatched_sells') or 0)} "
                f"rows(normal/token/internal)={int(hsrow.get('normal_rows') or 0)}/{int(hsrow.get('token_rows') or 0)}/{int(hsrow.get('internal_rows') or 0)} "
                f"diagnosis={diagnosis}"
            )


def _sol_candidate_detail(app, wallet: str) -> dict:
    with contextlib.closing(_sol.connect(app)) as conn:
        hs = conn.execute(
            """SELECT fetched_at,coverage_start_ts,coverage_end_ts,signatures,swaps,closed_trades,truncated,error
               FROM history_status WHERE wallet=?""",
            (str(wallet),),
        ).fetchone()
        cand = conn.execute(
            "SELECT first_seen,last_seen,swap_events,updated_at FROM candidates WHERE wallet=?",
            (str(wallet),),
        ).fetchone()
        life = conn.execute(
            "SELECT COUNT(*) n,MIN(sell_ts) first_sell,MAX(sell_ts) last_sell FROM trades WHERE wallet=?",
            (str(wallet),),
        ).fetchone()
    return {
        "history": dict(hs) if hs else {},
        "candidate": dict(cand) if cand else {},
        "lifetime_closed": int(life["n"] or 0) if life else 0,
    }


def report_solana(app) -> None:
    print("\n=== Solana SiBot leader gate + history-depth report ===")
    users = [u for u in _sol.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper() == "ACTIVE"]
    if not users:
        print("no enabled Solana account -- skipped")
        return
    tid = str(users[0].get("telegram_id") or "")
    cfg = _sol.settings(app)
    candidates = _sol.ranking_rows(app, tid)
    counts = {s: 0 for s in FUNNEL_STAGES}
    qualified = 0
    metrics: list[tuple[dict, dict, str | None]] = []
    for r in candidates:
        wallet = r.get("wallet")
        m = _sol_guard.quality_metrics(app, wallet, cfg)
        stage = _sol_stage_failed(m, cfg)
        counts[stage] += 1 if stage else 0
        qualified += 1 if stage is None else 0
        metrics.append((r, m, stage))
    _print_funnel(f"Solana (account {tid})", len(candidates), counts, qualified)
    _print_settings(
        "effective history settings",
        cfg,
        ["lookback_days", "min_closed_trades", "require_complete_history", "candidate_limit",
         "history_max_signatures", "history_refresh_hours", "rpc_delay_seconds", "discovery_blocks_per_cycle", "discovery_interval_seconds"],
    )
    with contextlib.closing(_sol.connect(app)) as conn:
        hs = conn.execute(
            """SELECT COUNT(*) n,SUM(CASE WHEN truncated=0 AND COALESCE(error,'')='' THEN 1 ELSE 0 END) complete,
                      SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,
                      MIN(fetched_at) oldest,MAX(fetched_at) newest,
                      SUM(signatures) signatures,SUM(swaps) swaps,SUM(closed_trades) closes
               FROM history_status"""
        ).fetchone()
        cs = conn.execute("SELECT COUNT(*) n,SUM(swap_events) events FROM candidates").fetchone()
    print(
        "  history store: "
        f"candidates={int(cs['n'] or 0)} discovery_swap_events={int(cs['events'] or 0)} "
        f"status_wallets={int(hs['n'] or 0)} complete={int(hs['complete'] or 0)} errors={int(hs['errors'] or 0)} "
        f"oldest_fetch_age={_age_hours(hs['oldest'])} newest_fetch_age={_age_hours(hs['newest'])} "
        f"signatures={int(hs['signatures'] or 0)} swaps={int(hs['swaps'] or 0)} closed_trades={int(hs['closes'] or 0)}"
    )
    print("  candidate detail:")
    for r, m, stage in metrics:
        wallet = str(r.get("wallet") or "")
        d = _sol_candidate_detail(app, wallet)
        hsrow, cand = d.get("history") or {}, d.get("candidate") or {}
        print(
            f"    #{r.get('rank','?')} {_short(wallet)} stage={stage or 'PASS'} "
            f"ranking_closed={int(r.get('closed_trades') or 0)} reconstructed={int(m.get('closed') or 0)} "
            f"history_status_closed={int(hsrow.get('closed_trades') or 0)} history_complete={bool(m.get('history_complete'))} "
            f"coverage={_coverage_days(hsrow.get('coverage_start_ts'), hsrow.get('coverage_end_ts')):.1f}d "
            f"fetch_age={_age_hours(hsrow.get('fetched_at'))} signatures={int(hsrow.get('signatures') or 0)} "
            f"swaps={int(hsrow.get('swaps') or 0)} truncated={int(hsrow.get('truncated') or 0)} "
            f"candidate_swap_events={int(cand.get('swap_events') or 0)} last_seen_age={_age_hours(cand.get('last_seen'))}"
        )


def main() -> int:
    app = AppSettings.load()
    report_evm(app)
    report_solana(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
