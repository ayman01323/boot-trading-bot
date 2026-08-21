from __future__ import annotations

import html
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__
from . import sibot as _evm
from . import solana_sibot as _sol
from . import telegram_sibot_patch as _telegram_sibot

LEGACY_VALUE = "legacy-unattributed"
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_MIGRATION_LOCK = threading.RLock()
_MIGRATED: set[tuple[str, str, str, str]] = set()

_ORIG_EVM_CONNECT = _evm.connect
_ORIG_SOL_CONNECT = _sol.connect
_PREV_REPORT_TEXT = _telegram_sibot.report_text


def _exact_git_sha() -> str:
    """Resolve the exact deployed commit; fail closed rather than misattribute trades."""
    for key in ("BOOT_GIT_SHA", "GIT_SHA", "GITHUB_SHA", "SOURCE_VERSION"):
        value = str(os.getenv(key) or "").strip().lower()
        if _SHA_RE.fullmatch(value):
            return value

    repo_root = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        value = proc.stdout.strip().lower()
        if _SHA_RE.fullmatch(value):
            return value
    except Exception:
        pass

    raise RuntimeError(
        "Trade provenance cannot resolve an exact Git SHA. "
        "Deploy from a Git checkout or set BOOT_GIT_SHA to the full commit SHA; "
        "trading will not start with ambiguous provenance."
    )


def _strategy_version() -> str:
    value = str(os.getenv("BOOT_STRATEGY_VERSION") or "").strip()
    return value or f"v{__version__}"


STRATEGY_VERSION = _strategy_version()
GIT_SHA = _exact_git_sha()


def current_identity() -> dict[str, str]:
    return {"strategy_version": STRATEGY_VERSION, "git_sha": GIT_SHA}


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _install_provenance_schema(conn: sqlite3.Connection, engine: str) -> None:
    """Migrate one positions table and enforce immutable entry-time provenance."""
    columns = _columns(conn, "positions")
    if not columns:
        raise RuntimeError(f"{engine} positions table is unavailable for trade provenance")

    conn.execute("BEGIN IMMEDIATE")
    try:
        if "strategy_version" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN strategy_version TEXT")
        if "git_sha" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN git_sha TEXT")

        # Never pretend pre-existing positions were opened by the current deployment.
        conn.execute(
            "UPDATE positions SET strategy_version=? "
            "WHERE strategy_version IS NULL OR TRIM(strategy_version)=''",
            (LEGACY_VALUE,),
        )
        conn.execute(
            "UPDATE positions SET git_sha=? WHERE git_sha IS NULL OR TRIM(git_sha)=''",
            (LEGACY_VALUE,),
        )

        stamp_trigger = f"trg_{engine}_positions_strategy_provenance_insert"
        immutable_trigger = f"trg_{engine}_positions_strategy_provenance_immutable"
        conn.execute(f"DROP TRIGGER IF EXISTS {stamp_trigger}")
        conn.execute(f"DROP TRIGGER IF EXISTS {immutable_trigger}")

        version_literal = _sql_literal(STRATEGY_VERSION)
        sha_literal = _sql_literal(GIT_SHA)
        conn.execute(
            f"""CREATE TRIGGER {stamp_trigger}
            AFTER INSERT ON positions
            WHEN NEW.strategy_version IS NULL OR TRIM(NEW.strategy_version) = ''
              OR NEW.git_sha IS NULL OR TRIM(NEW.git_sha) = ''
            BEGIN
              UPDATE positions
                 SET strategy_version = CASE
                       WHEN NEW.strategy_version IS NULL OR TRIM(NEW.strategy_version) = ''
                       THEN {version_literal} ELSE NEW.strategy_version END,
                     git_sha = CASE
                       WHEN NEW.git_sha IS NULL OR TRIM(NEW.git_sha) = ''
                       THEN {sha_literal} ELSE NEW.git_sha END
               WHERE position_id = NEW.position_id;
            END"""
        )
        conn.execute(
            f"""CREATE TRIGGER {immutable_trigger}
            BEFORE UPDATE OF strategy_version, git_sha ON positions
            WHEN (
                LENGTH(TRIM(COALESCE(OLD.strategy_version, ''))) > 0
                AND NEW.strategy_version IS NOT OLD.strategy_version
            ) OR (
                LENGTH(TRIM(COALESCE(OLD.git_sha, ''))) > 0
                AND NEW.git_sha IS NOT OLD.git_sha
            )
            BEGIN
              SELECT RAISE(ABORT, 'trade strategy provenance is immutable');
            END"""
        )
        conn.execute(
            f"""CREATE INDEX IF NOT EXISTS idx_{engine}_positions_strategy_24h
              ON positions(telegram_id, closed_at, mode, strategy_version, git_sha)"""
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _ensure_migrated(conn: sqlite3.Connection, engine: str, path: Path) -> None:
    key = (engine, str(Path(path).resolve()), STRATEGY_VERSION, GIT_SHA)
    if key in _MIGRATED:
        return
    with _MIGRATION_LOCK:
        if key in _MIGRATED:
            return
        _install_provenance_schema(conn, engine)
        _MIGRATED.add(key)


def _evm_connect_with_provenance(app) -> sqlite3.Connection:
    conn = _ORIG_EVM_CONNECT(app)
    try:
        _ensure_migrated(conn, "evm", _evm.db_path(app))
        return conn
    except Exception:
        conn.close()
        raise


def _sol_connect_with_provenance(app) -> sqlite3.Connection:
    conn = _ORIG_SOL_CONNECT(app)
    try:
        _ensure_migrated(conn, "solana", _sol.db_path(app))
        return conn
    except Exception:
        conn.close()
        raise


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _add_result(groups: dict, row: dict, *, chain: str, pnl: Decimal) -> None:
    version = str(row.get("strategy_version") or LEGACY_VALUE)
    sha = str(row.get("git_sha") or LEGACY_VALUE)
    mode = str(row.get("mode") or "UNKNOWN").upper()
    key = (mode, version, sha)
    if key not in groups:
        groups[key] = {
            "mode": mode,
            "strategy_version": version,
            "git_sha": sha,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "trades": 0,
            "latest_closed_at": 0,
            "pnl_by_chain": defaultdict(Decimal),
        }
    g = groups[key]
    g["trades"] += 1
    g["latest_closed_at"] = max(g["latest_closed_at"], int(row.get("closed_at") or 0))
    if pnl > 0:
        g["wins"] += 1
    elif pnl < 0:
        g["losses"] += 1
    else:
        g["breakeven"] += 1
    g["pnl_by_chain"][chain] += pnl


def strategy_attribution_24h(app, telegram_id, *, now: int | None = None) -> list[dict]:
    """Return 24-hour outcomes split by mode + exact strategy version + Git SHA."""
    cutoff = int(now if now is not None else time.time()) - 24 * 60 * 60
    groups: dict = {}

    with closing(_evm.connect(app)) as conn:
        rows = conn.execute(
            """SELECT chain_slug,mode,strategy_version,git_sha,closed_at,
                      realised_user_net_native,realised_net_native
                 FROM positions
                WHERE telegram_id=? AND closed_at IS NOT NULL AND closed_at>=?""",
            (str(telegram_id), cutoff),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            user_net = row.get("realised_user_net_native")
            pnl = _dec(user_net if user_net not in (None, "") else row.get("realised_net_native"))
            _add_result(groups, row, chain=str(row.get("chain_slug") or "evm"), pnl=pnl)

    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            """SELECT mode,strategy_version,git_sha,closed_at,realised_net_sol
                 FROM positions
                WHERE telegram_id=? AND closed_at IS NOT NULL AND closed_at>=?""",
            (str(telegram_id), cutoff),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            _add_result(groups, row, chain="solana", pnl=_dec(row.get("realised_net_sol")))

    out = list(groups.values())
    for g in out:
        g["pnl_by_chain"] = dict(g["pnl_by_chain"])
    out.sort(key=lambda g: (g["latest_closed_at"], g["mode"], g["strategy_version"], g["git_sha"]), reverse=True)
    return out


def _fmt_native(value: Decimal) -> str:
    text = f"{value:+.9f}".rstrip("0").rstrip(".")
    return text if text not in {"+0", "-0"} else "0"


def strategy_attribution_report_24h(app, telegram_id, *, now: int | None = None) -> str:
    groups = strategy_attribution_24h(app, telegram_id, now=now)
    lines = ["<b>🧬 24H STRATEGY ATTRIBUTION</b>"]
    if not groups:
        return "\n".join(lines + ["No closed bot positions in the last 24 hours."])

    for g in groups:
        decided = int(g["wins"]) + int(g["losses"])
        win_rate = (100.0 * int(g["wins"]) / decided) if decided else 0.0
        sha = str(g["git_sha"])
        sha_display = sha if sha == LEGACY_VALUE else sha[:12]
        lines += [
            "",
            f"• <b>{html.escape(g['mode'])}</b> • <b>{html.escape(g['strategy_version'])}</b> • <code>{html.escape(sha_display)}</code>",
            f"  ✅ W {g['wins']} • ❌ L {g['losses']} • ➖ BE {g['breakeven']} • win rate {win_rate:.1f}%",
        ]
        pnl_parts = [
            f"{html.escape(chain)} {_fmt_native(value)}"
            for chain, value in sorted(g["pnl_by_chain"].items())
        ]
        if pnl_parts:
            lines.append("  P&amp;L by chain (native): " + " • ".join(pnl_parts))

    lines += [
        "",
        "<i>Each line is isolated by LIVE/SHADOW mode, strategy version and exact opening Git SHA; different deployments are never merged into one win/loss count.</i>",
    ]
    return "\n".join(lines)


def report_text(app, tid):
    base = _PREV_REPORT_TEXT(app, tid)
    return base.rstrip() + "\n\n" + strategy_attribution_report_24h(app, tid)


def install() -> None:
    _evm.connect = _evm_connect_with_provenance
    _sol.connect = _sol_connect_with_provenance
    _telegram_sibot.report_text = report_text


install()
