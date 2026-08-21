#!/usr/bin/env python3
"""Read-only per-chain SiBot leader-quality gate funnel report.

This diagnostic intentionally refuses to run against the live repository tree.
The restricted VPS wrapper creates a temporary snapshot containing tracked
runtime code, a point-in-time copy of the live CSV configuration, and
consistent backups of the SiBot SQLite databases.  Only that snapshot is
analysed.

Within the snapshot the report loads the same patch chain as
``learnerbot/__main__.py`` so the thresholds and wrappers it observes match
the deployed runtime.  Normal database connectors and settings initialisers
are replaced with read-only equivalents before and after the patch-chain load.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

if os.getenv("SIBOT_GATE_SNAPSHOT") != "1":
    print(
        "Refusing to run SiBot leader-gate report outside the isolated snapshot. "
        "Use /usr/local/sbin/run-sibot-leader-gate-report via the GitHub workflow.",
        file=sys.stderr,
    )
    raise SystemExit(2)

# The wrapper deliberately excludes .env from the snapshot.  Disable dotenv
# loading as a second boundary so this diagnostic never imports production API
# keys even if an .env file is accidentally introduced into tracked code later.
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
    """Open an existing SQLite database with SQLite's read-only/query-only gates."""
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
    """Disable schema/config initialisation for this report process."""

    def sibot_settings_path(app):
        return Path(app.csv_dir) / "sibot_settings.csv"

    def solana_settings_path(app):
        return Path(app.csv_dir) / "solana_settings.csv"

    # Normal production connect() functions create directories, set WAL mode and
    # execute CREATE TABLE statements.  The diagnostic must never do that, even
    # to its temporary database copies.
    _sibot.connect = lambda app: _readonly_sqlite(_sibot.db_path(app))
    _sol.connect = lambda app: _readonly_sqlite(_sol.db_path(app))

    # Active settings wrappers eventually resolve these module globals and can
    # run one-time migrations.  Returning the existing path preserves reads of
    # effective settings while preventing migration/config writes.
    _sibot.ensure_settings = sibot_settings_path
    _sol.ensure_settings = solana_settings_path
    _sibot._atomic_csv = _blocked_config_write


def _load_patch_chain() -> None:
    """Replicate learnerbot/__main__.py's import order without invoking main()."""
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


# Guard the base modules before patch imports.  Some patches replace these
# functions, so reapply the guards after the complete runtime composition too.
_install_readonly_guards()
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


def report_evm(app) -> None:
    print("=== EVM SiBot leader gate report ===")
    for chain in load_chains(app, enabled_only=True):
        # ChainConfig normalises the configured type to uppercase; compare
        # case-insensitively so EVM chains cannot be silently skipped.
        if str(chain.type).strip().lower() != "evm":
            continue
        users = [
            u
            for u in _sibot.all_users(app.csv_dir, enabled_only=True)
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
        recent_n = max(5, _sibot._int(cfg.get("recent_trade_window"), 20))
        counts = {s: 0 for s in FUNNEL_STAGES}
        qualified = 0
        for r in candidates:
            m = _evm_guard.quality_metrics(
                app,
                chain.chain_id,
                r.get("wallet"),
                cfg.get("lookback_days", 60),
                recent_n,
            )
            stage = _evm_stage_failed(m, cfg)
            if stage is None:
                qualified += 1
            else:
                counts[stage] += 1
        _print_funnel(f"{chain.name} (account {tid})", len(candidates), counts, qualified)


def report_solana(app) -> None:
    print("\n=== Solana SiBot leader gate report ===")
    users = [
        u
        for u in _sol.all_users(app.csv_dir, enabled_only=True)
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
    for r in candidates:
        wallet = r.get("wallet")
        m = _sol_guard.quality_metrics(app, wallet, cfg)
        stage = _sol_stage_failed(m, cfg)
        if stage is None:
            qualified += 1
        else:
            counts[stage] += 1
    _print_funnel(f"Solana (account {tid})", len(candidates), counts, qualified)


def main() -> int:
    app = AppSettings.load()
    report_evm(app)
    report_solana(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
