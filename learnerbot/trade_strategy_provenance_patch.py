from __future__ import annotations

import csv
import html
import os
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import auto_trader as _auto
from . import sibot as _evm
from . import solana_sibot as _sol
from . import telegram_sibot_patch as _telegram_sibot
from .trade_provenance import GIT_SHA, LEGACY_VALUE, STRATEGY_VERSION, current_identity

EVM_ENGINE = "SIBOT_EVM_COPY"
SOLANA_ENGINE = "SIBOT_SOLANA_COPY"
AUTO_EVM_ENGINE = "AUTO_EVM_ARBITRAGE"

_MIGRATION_LOCK = threading.RLock()
_MIGRATED: set[tuple[str, str, str, str]] = set()

_ORIG_EVM_CONNECT = _evm.connect
_ORIG_SOL_CONNECT = _sol.connect
_PREV_REPORT_TEXT = _telegram_sibot.report_text


def current_strategy_identity() -> dict[str, str]:
    return current_identity()


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _install_provenance_schema(conn: sqlite3.Connection, engine: str, strategy_engine: str) -> None:
    """Migrate one positions table and enforce immutable entry-time provenance."""
    columns = _columns(conn, "positions")
    if not columns:
        raise RuntimeError(f"{engine} positions table is unavailable for trade provenance")

    conn.execute("BEGIN IMMEDIATE")
    try:
        if "strategy_engine" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN strategy_engine TEXT")
        if "strategy_version" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN strategy_version TEXT")
        if "git_sha" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN git_sha TEXT")

        # The storage engine identifies the strategy family even for historical rows,
        # but the version/SHA of pre-deployment rows must never be guessed.
        conn.execute(
            "UPDATE positions SET strategy_engine=? "
            "WHERE strategy_engine IS NULL OR TRIM(strategy_engine)=''",
            (strategy_engine,),
        )
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

        engine_literal = _sql_literal(strategy_engine)
        version_literal = _sql_literal(STRATEGY_VERSION)
        sha_literal = _sql_literal(GIT_SHA)
        conn.execute(
            f"""CREATE TRIGGER {stamp_trigger}
            AFTER INSERT ON positions
            WHEN NEW.strategy_engine IS NULL OR TRIM(NEW.strategy_engine) = ''
              OR NEW.strategy_version IS NULL OR TRIM(NEW.strategy_version) = ''
              OR NEW.git_sha IS NULL OR TRIM(NEW.git_sha) = ''
            BEGIN
              UPDATE positions
                 SET strategy_engine = CASE
                       WHEN NEW.strategy_engine IS NULL OR TRIM(NEW.strategy_engine) = ''
                       THEN {engine_literal} ELSE NEW.strategy_engine END,
                     strategy_version = CASE
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
            BEFORE UPDATE OF strategy_engine, strategy_version, git_sha ON positions
            WHEN (
                LENGTH(TRIM(COALESCE(OLD.strategy_engine, ''))) > 0
                AND NEW.strategy_engine IS NOT OLD.strategy_engine
            ) OR (
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
              ON positions(telegram_id, closed_at, mode, strategy_engine, strategy_version, git_sha)"""
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _ensure_migrated(
    conn: sqlite3.Connection,
    engine: str,
    strategy_engine: str,
    path: Path,
) -> None:
    key = (engine, str(Path(path).resolve()), STRATEGY_VERSION, GIT_SHA)
    if key in _MIGRATED:
        return
    with _MIGRATION_LOCK:
        if key in _MIGRATED:
            return
        _install_provenance_schema(conn, engine, strategy_engine)
        _MIGRATED.add(key)


def _evm_connect_with_provenance(app) -> sqlite3.Connection:
    conn = _ORIG_EVM_CONNECT(app)
    try:
        _ensure_migrated(conn, "evm", EVM_ENGINE, _evm.db_path(app))
        return conn
    except Exception:
        conn.close()
        raise


def _sol_connect_with_provenance(app) -> sqlite3.Connection:
    conn = _ORIG_SOL_CONNECT(app)
    try:
        _ensure_migrated(conn, "solana", SOLANA_ENGINE, _sol.db_path(app))
        return conn
    except Exception:
        conn.close()
        raise


def _normalise_existing_auto_row(row: dict, strategy_engine: str) -> dict:
    out = dict(row)
    out["strategy_engine"] = str(out.get("strategy_engine") or strategy_engine)
    out["strategy_version"] = str(out.get("strategy_version") or LEGACY_VALUE)
    out["git_sha"] = str(out.get("git_sha") or LEGACY_VALUE)
    return out


def _stamp_new_auto_row(row: dict, strategy_engine: str) -> dict:
    out = dict(row)
    out.update(current_identity(strategy_engine=strategy_engine))
    return out


def _atomic_bounded_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    rows = rows[-10000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{h: r.get(h, "") for h in headers} for r in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _auto_append_with_provenance(path: Path, row: dict) -> None:
    """Persist every AUTO EVM execution/attempt with immutable-at-write provenance."""
    path = Path(path)
    headers = [
        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
        "strategy_engine", "strategy_version", "git_sha",
        "route_id", "route_path", "input_base", "expected_gross_base",
        "expected_gas_base", "expected_net_base", "realised_net_base",
        "profit_fee_base", "fee_tx_hash", "tx_hash", "status", "note",
    ]
    existing = [
        _normalise_existing_auto_row(r, AUTO_EVM_ENGINE)
        for r in _auto._rows(path)
    ]
    existing.append(_stamp_new_auto_row(row, AUTO_EVM_ENGINE))
    _atomic_bounded_csv(path, existing, headers)


def _auto_append_simulation_with_provenance(csv_dir: Path, row: dict) -> None:
    """Stamp simulations too, so the evidence leading to a live trade is reproducible."""
    path = Path(csv_dir) / "auto" / "auto_trade_simulations.csv"
    headers = [
        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
        "strategy_engine", "strategy_version", "git_sha",
        "route_id", "route_path", "input_base", "min_net_profit_base",
        "gross_profit_base", "gas_cost_base", "simulation_ok", "reason",
    ]
    existing = [
        _normalise_existing_auto_row(r, AUTO_EVM_ENGINE)
        for r in _auto._rows(path)
    ]
    existing.append(_stamp_new_auto_row(row, AUTO_EVM_ENGINE))
    _atomic_bounded_csv(path, existing, headers)


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _add_result(
    groups: dict,
    row: dict,
    *,
    chain: str,
    pnl: Decimal,
    default_engine: str,
) -> None:
    engine = str(row.get("strategy_engine") or default_engine).upper()
    version = str(row.get("strategy_version") or LEGACY_VALUE)
    sha = str(row.get("git_sha") or LEGACY_VALUE)
    mode = str(row.get("mode") or "UNKNOWN").upper()
    key = (engine, mode, version, sha)
    if key not in groups:
        groups[key] = {
            "strategy_engine": engine,
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


def _auto_trade_rows_24h(app, telegram_id, cutoff: int) -> list[dict]:
    path = Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"
    out = []
    for raw in _auto._rows(path):
        if str(raw.get("telegram_id") or "").strip() != str(telegram_id):
            continue
        try:
            ts = int(float(raw.get("timestamp_epoch") or 0))
        except Exception:
            continue
        if ts < cutoff:
            continue
        # Only receipt-confirmed outcomes with a realised P&L belong in W/L counts.
        if str(raw.get("status") or "").upper() not in {"SUCCESS", "SUCCESS_FEE_PENDING"}:
            continue
        realised_raw = str(raw.get("realised_net_base") or "").strip()
        if not realised_raw:
            continue
        row = _normalise_existing_auto_row(raw, AUTO_EVM_ENGINE)
        row["mode"] = "LIVE"
        row["closed_at"] = ts
        out.append(row)
    return out


def strategy_attribution_24h(app, telegram_id, *, now: int | None = None) -> list[dict]:
    """Return 24-hour outcomes split by engine + mode + version + exact Git SHA."""
    cutoff = int(now if now is not None else time.time()) - 24 * 60 * 60
    groups: dict = {}

    with closing(_evm.connect(app)) as conn:
        rows = conn.execute(
            """SELECT chain_slug,mode,strategy_engine,strategy_version,git_sha,closed_at,
                      realised_user_net_native,realised_net_native
                 FROM positions
                WHERE telegram_id=? AND closed_at IS NOT NULL AND closed_at>=?""",
            (str(telegram_id), cutoff),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            user_net = row.get("realised_user_net_native")
            pnl = _dec(user_net if user_net not in (None, "") else row.get("realised_net_native"))
            _add_result(
                groups,
                row,
                chain=str(row.get("chain_slug") or "evm"),
                pnl=pnl,
                default_engine=EVM_ENGINE,
            )

    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            """SELECT mode,strategy_engine,strategy_version,git_sha,closed_at,realised_net_sol
                 FROM positions
                WHERE telegram_id=? AND closed_at IS NOT NULL AND closed_at>=?""",
            (str(telegram_id), cutoff),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            _add_result(
                groups,
                row,
                chain="solana",
                pnl=_dec(row.get("realised_net_sol")),
                default_engine=SOLANA_ENGINE,
            )

    for row in _auto_trade_rows_24h(app, telegram_id, cutoff):
        # AUTO realised_net_base is after cycle gas; subtract any platform
        # profit fee as well so attribution reflects the user's retained outcome.
        pnl = _dec(row.get("realised_net_base")) - _dec(row.get("profit_fee_base"))
        _add_result(
            groups,
            row,
            chain=str(row.get("chain_slug") or "evm"),
            pnl=pnl,
            default_engine=AUTO_EVM_ENGINE,
        )

    out = list(groups.values())
    for g in out:
        g["pnl_by_chain"] = dict(g["pnl_by_chain"])
    out.sort(
        key=lambda g: (
            g["latest_closed_at"],
            g["strategy_engine"],
            g["mode"],
            g["strategy_version"],
            g["git_sha"],
        ),
        reverse=True,
    )
    return out


def _fmt_native(value: Decimal) -> str:
    text = f"{value:+.9f}".rstrip("0").rstrip(".")
    return text if text not in {"+0", "-0"} else "0"


def strategy_attribution_report_24h(app, telegram_id, *, now: int | None = None) -> str:
    groups = strategy_attribution_24h(app, telegram_id, now=now)
    lines = ["<b>🧬 24H STRATEGY ATTRIBUTION</b>"]
    if not groups:
        return "\n".join(lines + ["No closed strategy trades in the last 24 hours."])

    for g in groups:
        decided = int(g["wins"]) + int(g["losses"])
        win_rate = (100.0 * int(g["wins"]) / decided) if decided else 0.0
        sha = str(g["git_sha"])
        sha_display = sha if sha == LEGACY_VALUE else sha[:12]
        lines += [
            "",
            f"• <b>{html.escape(g['strategy_engine'])}</b> • <b>{html.escape(g['mode'])}</b>",
            f"  version <b>{html.escape(g['strategy_version'])}</b> • SHA <code>{html.escape(sha_display)}</code>",
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
        "<i>Results are isolated by strategy engine, LIVE/SHADOW mode, strategy version and exact opening Git SHA; different strategies or deployments are never merged into one win/loss count.</i>",
    ]
    return "\n".join(lines)


def report_text(app, tid):
    base = _PREV_REPORT_TEXT(app, tid)
    return base.rstrip() + "\n\n" + strategy_attribution_report_24h(app, tid)


def install() -> None:
    _evm.connect = _evm_connect_with_provenance
    _sol.connect = _sol_connect_with_provenance
    _auto._append = _auto_append_with_provenance
    _auto._append_simulation = _auto_append_simulation_with_provenance
    _telegram_sibot.report_text = report_text


install()
