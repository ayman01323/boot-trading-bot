from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import auto_trader as _auto
from . import live_executor as _live
from . import sibot as _evm
from . import solana_sibot as _sol
from . import telegram_sibot_patch as _telegram_sibot
from .trade_provenance import GIT_SHA, LEGACY_VALUE, STRATEGY_VERSION, current_identity

EVM_ENGINE = "SIBOT_EVM_COPY"
SOLANA_ENGINE = "SIBOT_SOLANA_COPY"
AUTO_EVM_ENGINE = "AUTO_EVM_ARBITRAGE"
MANUAL_EVM_ENGINE = "MANUAL_EVM"
EVM_WALLET_OPERATION_ENGINE = "EVM_WALLET_OPERATION"

_MIGRATION_LOCK = threading.RLock()
_MIGRATED: set[tuple[str, str, str, str]] = set()
_LEDGER_LOCK = threading.RLock()

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


def _ledger_path(csv_dir: Path) -> Path:
    return Path(csv_dir) / "auto" / "trade_provenance.sqlite3"


_LEDGER_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS trade_events(
  event_id TEXT PRIMARY KEY,
  event_ts INTEGER NOT NULL,
  telegram_id TEXT NOT NULL,
  wallet_id TEXT,
  chain_id TEXT,
  chain_slug TEXT,
  strategy_engine TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  git_sha TEXT NOT NULL,
  action TEXT NOT NULL,
  tx_hash TEXT,
  status TEXT NOT NULL,
  realised_pnl TEXT,
  profit_fee TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_events_24h
  ON trade_events(telegram_id,event_ts,strategy_engine,strategy_version,git_sha,action,status);
CREATE TABLE IF NOT EXISTS provenance_state(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_trade_events_no_update
BEFORE UPDATE ON trade_events
BEGIN
  SELECT RAISE(ABORT, 'trade provenance ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_trade_events_no_delete
BEFORE DELETE ON trade_events
BEGIN
  SELECT RAISE(ABORT, 'trade provenance ledger is append-only');
END;
"""


def _ledger_connect(csv_dir: Path) -> sqlite3.Connection:
    path = _ledger_path(csv_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_LEDGER_SCHEMA)
    return conn


def _event_id(row: dict, action: str) -> str:
    tx_hash = str(row.get("tx_hash") or "").strip().lower()
    engine = str(row.get("strategy_engine") or "UNKNOWN").upper()
    chain_id = str(row.get("chain_id") or "")
    if tx_hash:
        raw = f"{engine}|{chain_id}|{tx_hash}|{action.upper()}"
    else:
        raw = json.dumps(
            {"action": action.upper(), **{str(k): str(v) for k, v in sorted(row.items())}},
            sort_keys=True,
            separators=(",", ":"),
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_ledger_event(
    csv_dir: Path,
    row: dict,
    *,
    action: str,
    realised_pnl: str = "",
    profit_fee: str = "",
    metadata: dict | None = None,
) -> None:
    event_ts = int(float(row.get("timestamp_epoch") or time.time()))
    identity = current_identity(strategy_engine=str(row.get("strategy_engine") or "UNKNOWN"))
    # Historical/migrated rows deliberately retain legacy provenance rather than
    # being relabelled with the currently running deployment.
    version = str(row.get("strategy_version") or identity["strategy_version"])
    sha = str(row.get("git_sha") or identity["git_sha"])
    engine = str(row.get("strategy_engine") or identity["strategy_engine"]).upper()
    material = dict(row)
    material["strategy_engine"] = engine
    material["strategy_version"] = version
    material["git_sha"] = sha
    event_id = _event_id(material, action)
    with _LEDGER_LOCK, closing(_ledger_connect(csv_dir)) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO trade_events(
                 event_id,event_ts,telegram_id,wallet_id,chain_id,chain_slug,
                 strategy_engine,strategy_version,git_sha,action,tx_hash,status,
                 realised_pnl,profit_fee,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                event_ts,
                str(material.get("telegram_id") or "LEGACY"),
                str(material.get("wallet_id") or ""),
                str(material.get("chain_id") or ""),
                str(material.get("chain_slug") or ""),
                engine,
                version,
                sha,
                str(action).upper(),
                str(material.get("tx_hash") or ""),
                str(material.get("status") or "UNKNOWN").upper(),
                str(realised_pnl or ""),
                str(profit_fee or ""),
                json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), default=str),
                int(time.time()),
            ),
        )
        conn.commit()


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


def _atomic_bounded_csv(path: Path, rows: list[dict], headers: list[str], *, keep: int = 10000) -> None:
    rows = rows[-max(1, int(keep)):]
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
    """Persist AUTO EVM outcome provenance before rotating the operational CSV."""
    path = Path(path)
    stamped = _stamp_new_auto_row(row, AUTO_EVM_ENGINE)
    csv_dir = path.parent.parent
    _record_ledger_event(
        csv_dir,
        stamped,
        action="AUTO_OUTCOME",
        realised_pnl=str(stamped.get("realised_net_base") or ""),
        profit_fee=str(stamped.get("profit_fee_base") or ""),
        metadata={
            "route_id": stamped.get("route_id") or "",
            "route_path": stamped.get("route_path") or "",
            "input_base": stamped.get("input_base") or "",
            "note": stamped.get("note") or "",
        },
    )
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
    existing.append(stamped)
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


def _engine_for_live_action(action: str) -> str:
    action = str(action or "").upper()
    if action.startswith("AUTO_"):
        return AUTO_EVM_ENGINE
    if action in {"BUY", "SELL"}:
        return MANUAL_EVM_ENGINE
    return EVM_WALLET_OPERATION_ENGINE


def _live_audit_with_provenance(
    self,
    side,
    token,
    symbol,
    amount_in,
    expected_out,
    minimum_out,
    tx_hash,
    status,
    approval_hash="",
):
    """Stamp every LiveTrader audit event and persist it in the append-only ledger."""
    action = str(side or "UNKNOWN").upper()
    engine = _engine_for_live_action(action)
    row = {
        "timestamp_epoch": int(time.time()),
        "telegram_id": self.telegram_id or "LEGACY",
        "wallet_id": self.wallet_id or "LEGACY",
        "chain_id": self.chain.chain_id,
        "chain_slug": self.chain.slug,
        "wallet": self.address or "",
        "side": action,
        "token": token,
        "symbol": symbol,
        "amount_in": amount_in,
        "expected_out": expected_out,
        "minimum_out": minimum_out,
        "router": self.router_address,
        "tx_hash": tx_hash,
        "approval_hash": approval_hash,
        "status": status,
        **current_identity(strategy_engine=engine),
    }
    _record_ledger_event(
        self.app.csv_dir,
        row,
        action=action,
        metadata={
            "token": token,
            "symbol": symbol,
            "amount_in": amount_in,
            "expected_out": expected_out,
            "minimum_out": minimum_out,
            "router": self.router_address,
            "approval_hash": approval_hash,
        },
    )

    audit_path = Path(self.app.csv_dir) / "auto" / "live_trade_audit.csv"
    headers = [
        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
        "strategy_engine", "strategy_version", "git_sha",
        "wallet", "side", "token", "symbol", "amount_in", "expected_out",
        "minimum_out", "router", "tx_hash", "approval_hash", "status",
    ]
    existing = []
    for old in _auto._rows(audit_path):
        old_engine = _engine_for_live_action(str(old.get("side") or ""))
        existing.append(_normalise_existing_auto_row(old, old_engine))
    existing.append(row)
    _atomic_bounded_csv(audit_path, existing, headers, keep=5000)


def _migrate_auto_operational_csv_to_ledger(app) -> None:
    """Preserve the still-available pre-feature AUTO history as legacy evidence once."""
    csv_dir = Path(app.csv_dir)
    key = "auto_operational_csv_migrated_v1"
    with _LEDGER_LOCK, closing(_ledger_connect(csv_dir)) as conn:
        state = conn.execute("SELECT value FROM provenance_state WHERE key=?", (key,)).fetchone()
        if state:
            return

    path = csv_dir / "auto" / "auto_trade_execution.csv"
    for raw in _auto._rows(path):
        row = _normalise_existing_auto_row(raw, AUTO_EVM_ENGINE)
        _record_ledger_event(
            csv_dir,
            row,
            action="AUTO_OUTCOME",
            realised_pnl=str(row.get("realised_net_base") or ""),
            profit_fee=str(row.get("profit_fee_base") or ""),
            metadata={
                "route_id": row.get("route_id") or "",
                "route_path": row.get("route_path") or "",
                "input_base": row.get("input_base") or "",
                "note": row.get("note") or "",
                "migrated_from_operational_csv": True,
            },
        )

    with _LEDGER_LOCK, closing(_ledger_connect(csv_dir)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO provenance_state(key,value) VALUES(?,?)",
            (key, str(int(time.time()))),
        )
        conn.commit()


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
    _migrate_auto_operational_csv_to_ledger(app)
    with closing(_ledger_connect(app.csv_dir)) as conn:
        rows = conn.execute(
            """SELECT event_ts AS closed_at,chain_slug,strategy_engine,strategy_version,git_sha,
                      status,realised_pnl AS realised_net_base,profit_fee AS profit_fee_base
                 FROM trade_events
                WHERE telegram_id=? AND event_ts>=? AND action='AUTO_OUTCOME'
                  AND status IN ('SUCCESS','SUCCESS_FEE_PENDING')
                  AND TRIM(COALESCE(realised_pnl,''))<>''
                ORDER BY event_ts""",
            (str(telegram_id), int(cutoff)),
        ).fetchall()
    out = []
    for raw in rows:
        row = dict(raw)
        row["mode"] = "LIVE"
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
    _live.LiveTrader._audit = _live_audit_with_provenance
    _telegram_sibot.report_text = report_text


install()
