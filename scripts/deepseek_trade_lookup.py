#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path("/root/multichain-learning-bot-v2.2-fast-direct-market")
CSV_DIR = RUNTIME_ROOT / "CSVbot"
DATA_DIR = RUNTIME_ROOT / "data"

PROVENANCE_DB = CSV_DIR / "auto" / "trade_provenance.sqlite3"
AUTO_EXECUTION_CSV = CSV_DIR / "auto" / "auto_trade_execution.csv"
AUTO_SIMULATION_CSV = CSV_DIR / "auto" / "auto_trade_simulations.csv"
EVM_SIBOT_DB = DATA_DIR / "sibot.sqlite3"
SOLANA_SIBOT_DB = DATA_DIR / "solana_sibot.sqlite3"

ACCOUNT_RE = re.compile(r"^[0-9]{1,20}$")
EXACT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SAFE_METADATA_KEYS = {
    "route_id", "route_path", "input_base", "note", "mint", "symbol",
    "leader_rank", "exit_reason", "simulation_ok", "reason",
}


def validate_account_id(value: str) -> bool:
    return bool(ACCOUNT_RE.fullmatch(str(value or "").strip()))


def validate_exact_id(value: str) -> bool:
    return bool(EXACT_RE.fullmatch(str(value or "").strip()))


def _db_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _select_rows(
    path: Path,
    table: str,
    wanted_columns: tuple[str, ...],
    where_sql: str,
    params: tuple[Any, ...],
    order_column: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with _db_connect(path) as conn:
            available = _table_columns(conn, table)
            cols = [c for c in wanted_columns if c in available]
            if not cols or order_column not in available:
                return []
            sql = (
                f"SELECT {','.join(cols)} FROM {table} "
                f"WHERE {where_sql} ORDER BY {order_column} DESC LIMIT ?"
            )
            rows = conn.execute(sql, (*params, max(1, min(int(limit), 100)))).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _normalise_metadata(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    return {k: value.get(k) for k in SAFE_METADATA_KEYS if k in value}


def _provenance_account(telegram_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = _select_rows(
        PROVENANCE_DB,
        "trade_events",
        (
            "event_id", "event_ts", "telegram_id", "wallet_id", "chain_id", "chain_slug",
            "strategy_engine", "strategy_version", "git_sha", "action", "tx_hash",
            "status", "realised_pnl", "profit_fee", "metadata_json",
        ),
        "telegram_id=?",
        (telegram_id,),
        "event_ts",
        limit,
    )
    out = []
    for row in rows:
        metadata = _normalise_metadata(row.pop("metadata_json", "{}"))
        out.append({
            "source": "trade_provenance",
            "record_kind": "trade_event",
            "record_id": row.pop("event_id", ""),
            "timestamp_epoch": row.pop("event_ts", 0),
            **row,
            "metadata": metadata,
        })
    return out


def _provenance_exact(identifier: str) -> list[dict[str, Any]]:
    if not PROVENANCE_DB.exists():
        return []
    wanted = (
        "event_id", "event_ts", "telegram_id", "wallet_id", "chain_id", "chain_slug",
        "strategy_engine", "strategy_version", "git_sha", "action", "tx_hash",
        "status", "realised_pnl", "profit_fee", "metadata_json",
    )
    out: list[dict[str, Any]] = []
    try:
        with _db_connect(PROVENANCE_DB) as conn:
            available = _table_columns(conn, "trade_events")
            cols = [c for c in wanted if c in available]
            if not cols:
                return []
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM trade_events "
                "WHERE event_id=? OR tx_hash=? ORDER BY event_ts DESC LIMIT 20",
                (identifier, identifier),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                metadata = _normalise_metadata(row.pop("metadata_json", "{}"))
                out.append({
                    "source": "trade_provenance",
                    "record_kind": "trade_event",
                    "record_id": row.pop("event_id", ""),
                    "timestamp_epoch": row.pop("event_ts", 0),
                    **row,
                    "metadata": metadata,
                })
    except sqlite3.Error:
        return []
    return out


EVM_POSITION_COLUMNS = (
    "position_id", "telegram_id", "chain_id", "chain_slug", "token", "symbol",
    "mode", "status", "token_amount_raw", "entry_input_native", "entry_cost_native",
    "entry_tx", "entry_ts", "current_exit_native", "unrealised_net_native",
    "unrealised_pct", "peak_unrealised_pct", "realised_net_native",
    "realised_user_net_native", "profit_fee_native", "exit_tx", "exit_reason",
    "closed_at", "updated_at", "strategy_engine", "strategy_version", "git_sha",
)

SOL_POSITION_COLUMNS = (
    "position_id", "telegram_id", "mint", "mode", "status", "token_amount_raw",
    "entry_cost_sol", "entry_ts", "leader_buy_signature", "current_exit_sol",
    "unrealised_net_sol", "unrealised_pct", "peak_unrealised_pct",
    "realised_net_sol", "exit_signature", "exit_reason", "closed_at", "updated_at",
    "strategy_engine", "strategy_version", "git_sha",
)


def _positions_account(path: Path, columns: tuple[str, ...], telegram_id: str, source: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = _select_rows(path, "positions", columns, "telegram_id=?", (telegram_id,), "entry_ts", limit)
    return [{
        "source": source,
        "record_kind": "position",
        "record_id": row.get("position_id", ""),
        "timestamp_epoch": row.get("entry_ts", 0),
        **row,
    } for row in rows]


def _positions_exact(path: Path, columns: tuple[str, ...], identifier: str, source: str) -> list[dict[str, Any]]:
    rows = _select_rows(path, "positions", columns, "position_id=?", (identifier,), "entry_ts", 5)
    return [{
        "source": source,
        "record_kind": "position",
        "record_id": row.get("position_id", ""),
        "timestamp_epoch": row.get("entry_ts", 0),
        **row,
    } for row in rows]


def _sol_live_attempts_account(telegram_id: str, limit: int = 10) -> list[dict[str, Any]]:
    columns = (
        "attempt_key", "telegram_id", "mint", "action", "status", "tx_signature",
        "input_raw", "output_raw", "wallet_delta_lamports", "error", "created_at", "updated_at",
    )
    rows = _select_rows(
        SOLANA_SIBOT_DB, "live_execution_attempts", columns,
        "telegram_id=?", (telegram_id,), "created_at", limit,
    )
    return [{
        "source": "solana_live_attempts",
        "record_kind": "execution_attempt",
        "record_id": row.get("attempt_key", ""),
        "timestamp_epoch": row.get("created_at", 0),
        **row,
    } for row in rows]


def _sol_live_attempts_exact(identifier: str) -> list[dict[str, Any]]:
    columns = (
        "attempt_key", "telegram_id", "mint", "action", "status", "tx_signature",
        "input_raw", "output_raw", "wallet_delta_lamports", "error", "created_at", "updated_at",
    )
    if not SOLANA_SIBOT_DB.exists():
        return []
    try:
        with _db_connect(SOLANA_SIBOT_DB) as conn:
            available = _table_columns(conn, "live_execution_attempts")
            cols = [c for c in columns if c in available]
            if not cols:
                return []
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM live_execution_attempts "
                "WHERE attempt_key=? OR tx_signature=? ORDER BY created_at DESC LIMIT 10",
                (identifier, identifier),
            ).fetchall()
            out = []
            for raw in rows:
                row = dict(raw)
                out.append({
                    "source": "solana_live_attempts",
                    "record_kind": "execution_attempt",
                    "record_id": row.get("attempt_key", ""),
                    "timestamp_epoch": row.get("created_at", 0),
                    **row,
                })
            return out
    except sqlite3.Error:
        return []


def _historical_trade_exact(path: Path, table: str, identifier: str, source: str, timestamp_col: str, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = _select_rows(path, table, columns, "trade_id=?", (identifier,), timestamp_col, 5)
    return [{
        "source": source,
        "record_kind": "historical_leader_trade",
        "record_id": row.get("trade_id", ""),
        "timestamp_epoch": row.get(timestamp_col, 0),
        **row,
    } for row in rows]


def _csv_account(path: Path, telegram_id: str, source: str, limit: int = 10) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("telegram_id") or "").strip() != telegram_id:
                    continue
                if source == "auto_trade_execution":
                    keys = (
                        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
                        "strategy_engine", "strategy_version", "git_sha", "route_id", "route_path",
                        "input_base", "expected_gross_base", "expected_gas_base", "expected_net_base",
                        "realised_net_base", "profit_fee_base", "fee_tx_hash", "tx_hash", "status", "note",
                    )
                else:
                    keys = (
                        "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
                        "strategy_engine", "strategy_version", "git_sha", "route_id", "route_path",
                        "input_base", "min_net_profit_base", "gross_profit_base", "gas_cost_base",
                        "simulation_ok", "reason",
                    )
                clean = {k: row.get(k, "") for k in keys if k in row}
                rows.append({
                    "source": source,
                    "record_kind": "csv_row",
                    "record_id": str(clean.get("tx_hash") or clean.get("timestamp_epoch") or ""),
                    "timestamp_epoch": clean.get("timestamp_epoch") or 0,
                    **clean,
                })
    except (OSError, csv.Error):
        return []
    rows.sort(key=lambda r: _ts(r.get("timestamp_epoch")), reverse=True)
    return rows[:max(1, min(limit, 50))]


def _ts(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _read_git_sha(root: Path) -> str:
    head = root / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", text):
            return text
        if text.startswith("ref: "):
            ref = text[5:].strip()
            ref_path = root / ".git" / ref
            if ref_path.exists():
                value = ref_path.read_text(encoding="utf-8").strip()
                if re.fullmatch(r"[0-9a-f]{40}", value):
                    return value
            packed = root / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("#") or line.startswith("^") or " " not in line:
                        continue
                    sha, name = line.split(" ", 1)
                    if name.strip() == ref and re.fullmatch(r"[0-9a-f]{40}", sha):
                        return sha
    except OSError:
        pass
    return ""


def account_lookup(telegram_id: str) -> dict[str, Any]:
    if not validate_account_id(telegram_id):
        raise ValueError("invalid telegram_id")
    records: list[dict[str, Any]] = []
    records += _provenance_account(telegram_id, 20)
    records += _positions_account(EVM_SIBOT_DB, EVM_POSITION_COLUMNS, telegram_id, "evm_sibot_positions", 10)
    records += _positions_account(SOLANA_SIBOT_DB, SOL_POSITION_COLUMNS, telegram_id, "solana_sibot_positions", 10)
    records += _sol_live_attempts_account(telegram_id, 10)
    records += _csv_account(AUTO_EXECUTION_CSV, telegram_id, "auto_trade_execution", 10)
    records += _csv_account(AUTO_SIMULATION_CSV, telegram_id, "auto_trade_simulation", 10)
    records.sort(key=lambda r: _ts(r.get("timestamp_epoch")), reverse=True)
    records = records[:40]
    return {
        "lookup_type": "account",
        "identifier": telegram_id,
        "deployed_sha": _read_git_sha(RUNTIME_ROOT),
        "records": records,
        "count": len(records),
        "not_found": not bool(records),
    }


def exact_lookup(identifier: str) -> dict[str, Any]:
    if not validate_exact_id(identifier):
        raise ValueError("invalid position/trade/event identifier")
    records: list[dict[str, Any]] = []
    records += _provenance_exact(identifier)
    records += _positions_exact(EVM_SIBOT_DB, EVM_POSITION_COLUMNS, identifier, "evm_sibot_positions")
    records += _positions_exact(SOLANA_SIBOT_DB, SOL_POSITION_COLUMNS, identifier, "solana_sibot_positions")
    records += _sol_live_attempts_exact(identifier)
    records += _historical_trade_exact(
        EVM_SIBOT_DB, "wallet_trades", identifier, "evm_leader_history", "sell_ts",
        (
            "trade_id", "chain_id", "chain_slug", "token", "symbol", "buy_tx", "sell_tx",
            "buy_ts", "sell_ts", "token_amount_raw", "cost_native", "proceeds_native",
            "buy_gas_native", "sell_gas_native", "net_native", "source", "updated_at",
        ),
    )
    records += _historical_trade_exact(
        SOLANA_SIBOT_DB, "trades", identifier, "solana_leader_history", "sell_ts",
        (
            "trade_id", "mint", "decimals", "buy_signature", "sell_signature",
            "buy_ts", "sell_ts", "token_amount_raw", "cost_sol", "proceeds_sol",
            "net_sol", "hold_seconds", "source", "updated_at",
        ),
    )
    records.sort(key=lambda r: _ts(r.get("timestamp_epoch")), reverse=True)
    records = records[:40]
    return {
        "lookup_type": "exact",
        "identifier": identifier,
        "deployed_sha": _read_git_sha(RUNTIME_ROOT),
        "records": records,
        "count": len(records),
        "not_found": not bool(records),
    }


def main() -> int:
    import os

    lookup_type = str(os.environ.get("LOOKUP_TYPE") or "").strip().lower()
    identifier = str(os.environ.get("IDENTIFIER") or "").strip()
    try:
        if lookup_type == "account":
            result = account_lookup(identifier)
        elif lookup_type == "exact":
            result = exact_lookup(identifier)
        else:
            raise ValueError("lookup_type must be account or exact")
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "lookup_type": lookup_type}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
