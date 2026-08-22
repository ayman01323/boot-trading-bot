from __future__ import annotations

import sqlite3

from . import solana_incident_forensics_patch as _incident


def _expr(cols: set[str], name: str, default_sql: str) -> str:
    return name if name in cols else f"{default_sql} AS {name}"


def position_rows_schema_compatible(conn: sqlite3.Connection) -> list[dict]:
    """Read modern and retained legacy LIVE-position schemas without mutating either.

    Historical/test databases predate account/leader/provenance columns. Missing
    observational fields must degrade to explicit unknowns rather than making the
    forensic report fail. Core P/L fields remain read from the database itself.
    """
    if not _incident._table(conn, "positions"):
        return []
    cols = _incident._cols(conn, "positions")
    required = {
        "position_id", "mint", "mode", "status", "token_amount_raw",
        "entry_cost_sol", "entry_ts", "current_exit_sol", "unrealised_net_sol",
        "unrealised_pct", "peak_unrealised_pct", "realised_net_sol",
        "exit_reason", "closed_at", "updated_at", "exit_signature",
    }
    if not required.issubset(cols):
        return []
    selected = [
        "position_id",
        _expr(cols, "telegram_id", "''"),
        _expr(cols, "leader_wallet", "''"),
        _expr(cols, "leader_rank", "0"),
        "mint", "status", "token_amount_raw", "entry_cost_sol", "entry_ts",
        "current_exit_sol", "unrealised_net_sol", "unrealised_pct",
        "peak_unrealised_pct", "realised_net_sol", "exit_reason", "closed_at",
        "updated_at", _expr(cols, "leader_buy_signature", "''"), "exit_signature",
        _expr(cols, "strategy_engine", "'LEGACY_UNKNOWN'"),
        _expr(cols, "strategy_version", "'LEGACY_UNKNOWN'"),
        _expr(cols, "git_sha", "'LEGACY_UNKNOWN'"),
    ]
    sql = (
        "SELECT " + ",".join(selected) + " FROM positions "
        "WHERE mode='LIVE' AND (entry_ts>=? OR COALESCE(closed_at,0)>=? OR status='OPEN') "
        "ORDER BY entry_ts"
    )
    return [dict(r) for r in conn.execute(sql, (_incident._INCIDENT_START, _incident._INCIDENT_START)).fetchall()]


def install() -> None:
    _incident._position_rows = position_rows_schema_compatible


install()
