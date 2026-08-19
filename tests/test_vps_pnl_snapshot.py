from __future__ import annotations

import json
import sqlite3
import time
import warnings
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _d(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _cols(conn, table):
    return {str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _exists(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def test_vps_sanitized_pnl_snapshot_probe():
    """One-time production diagnostic; emits no wallet/user/key material."""
    root = Path(__file__).resolve().parents[1]
    sol_db = root / "data" / "solana_sibot.sqlite3"
    if not sol_db.exists():
        return

    now = int(time.time())
    cutoff = now - 24 * 3600
    conn = sqlite3.connect(f"file:{sol_db}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        pcols = _cols(conn, "positions") if _exists(conn, "positions") else set()
        wanted = [
            "position_id", "mint", "mode", "status", "entry_cost_sol", "entry_ts",
            "realised_net_sol", "exit_signature", "exit_reason", "closed_at", "updated_at",
            "token_amount_raw", "current_exit_sol", "unrealised_net_sol", "unrealised_pct",
            "peak_unrealised_pct",
        ]
        use = [c for c in wanted if c in pcols]
        closed = []
        opened = []
        if use:
            select = ",".join(f'"{c}"' for c in use)
            where_closed = "status='CLOSED'"
            args = []
            if "mode" in pcols:
                where_closed += " AND mode='LIVE'"
            if "closed_at" in pcols:
                where_closed += " AND COALESCE(closed_at,0)>=?"
                args.append(cutoff)
            closed = [dict(r) for r in conn.execute(
                f"SELECT {select} FROM positions WHERE {where_closed} ORDER BY COALESCE(closed_at,0) DESC LIMIT 1000",
                args,
            ).fetchall()]

            where_open = "status='OPEN'"
            if "mode" in pcols:
                where_open += " AND mode='LIVE'"
            opened = [dict(r) for r in conn.execute(
                f"SELECT {select} FROM positions WHERE {where_open} ORDER BY COALESCE(updated_at,0) DESC LIMIT 100",
            ).fetchall()]

        vals = [_d(r.get("realised_net_sol")) for r in closed]
        wins = [v for v in vals if v > 0]
        losses = [-v for v in vals if v < 0]
        gp = sum(wins, Decimal(0))
        gl = sum(losses, Decimal(0))
        pf = gp / gl if gl > 0 else (Decimal(99) if gp > 0 else Decimal(0))
        reasons = Counter(str(r.get("exit_reason") or "UNKNOWN")[:160] for r in closed)

        def compact_position(r):
            return {
                k: r.get(k)
                for k in (
                    "position_id", "mint", "entry_cost_sol", "entry_ts", "realised_net_sol",
                    "exit_signature", "exit_reason", "closed_at", "updated_at", "token_amount_raw",
                    "current_exit_sol", "unrealised_net_sol", "unrealised_pct", "peak_unrealised_pct",
                )
                if k in r
            }

        worst = sorted(closed, key=lambda r: _d(r.get("realised_net_sol")))[:20]
        best = sorted(closed, key=lambda r: _d(r.get("realised_net_sol")), reverse=True)[:10]

        circuits = []
        circuit_counts = Counter()
        if _exists(conn, "live_exit_circuit"):
            ccols = _cols(conn, "live_exit_circuit")
            cwanted = [
                "position_id", "status", "tx_signature", "fraction", "close_reason",
                "sell_raw", "opened_at", "updated_at",
            ]
            cuse = [c for c in cwanted if c in ccols]
            if cuse:
                select = ",".join(f'"{c}"' for c in cuse)
                where = "1=1"
                args = []
                if "updated_at" in ccols:
                    where += " AND COALESCE(updated_at,0)>=?"
                    args.append(cutoff)
                circuits = [dict(r) for r in conn.execute(
                    f"SELECT {select} FROM live_exit_circuit WHERE {where} ORDER BY COALESCE(updated_at,0) DESC LIMIT 100",
                    args,
                ).fetchall()]
                circuit_counts.update(str(r.get("status") or "UNKNOWN").upper() for r in circuits)
    finally:
        conn.close()

    control = {"available": False}
    pc_db = root / "data" / "profit_control_loop.sqlite3"
    if pc_db.exists():
        pc = sqlite3.connect(f"file:{pc_db}?mode=ro", uri=True, timeout=10)
        pc.row_factory = sqlite3.Row
        try:
            control = {"available": True, "state": {}, "recent_runs": [], "strategies": []}
            if _exists(pc, "control_state"):
                control["state"] = {str(r["key"]): str(r["value"]) for r in pc.execute("SELECT key,value FROM control_state").fetchall()}
            if _exists(pc, "control_runs"):
                cols = _cols(pc, "control_runs")
                wanted = [
                    "generated_at", "profile", "closed_trades", "wins", "losses",
                    "gross_profit_sol", "gross_loss_sol", "net_sol", "profit_factor",
                    "profile_changed", "previous_profile", "gpt_status",
                ]
                use = [c for c in wanted if c in cols]
                control["recent_runs"] = [dict(r) for r in pc.execute(
                    f"SELECT {','.join(use)} FROM control_runs ORDER BY generated_at DESC LIMIT 24"
                ).fetchall()] if use else []
            if _exists(pc, "strategy_registry"):
                cols = _cols(pc, "strategy_registry")
                wanted = [
                    "profile", "hours_observed", "closed_trades", "wins", "losses",
                    "gross_profit_sol", "gross_loss_sol", "net_sol", "profit_factor",
                    "successful", "last_used_at", "updated_at",
                ]
                use = [c for c in wanted if c in cols]
                control["strategies"] = [dict(r) for r in pc.execute(
                    f"SELECT {','.join(use)} FROM strategy_registry ORDER BY updated_at DESC"
                ).fetchall()] if use else []
        finally:
            pc.close()

    snapshot = {
        "window_hours": 24,
        "performance": {
            "closed_trades": len(vals),
            "wins": len(wins),
            "losses": len(losses),
            "gross_profit_sol": str(gp),
            "gross_loss_sol": str(gl),
            "net_sol": str(gp - gl),
            "profit_factor": str(pf),
            "profit_amount_exceeds_loss_amount": gp > gl,
            "average_win_sol": str(gp / len(wins)) if wins else "0",
            "average_loss_sol": str(gl / len(losses)) if losses else "0",
            "largest_win_sol": str(max(wins)) if wins else "0",
            "largest_loss_sol": str(max(losses)) if losses else "0",
            "exit_reason_counts": dict(reasons.most_common(20)),
        },
        "worst_closed": [compact_position(r) for r in worst],
        "best_closed": [compact_position(r) for r in best],
        "open_live": [compact_position(r) for r in opened[:30]],
        "exit_circuit_status_counts": dict(circuit_counts),
        "recent_exit_circuits": circuits[:30],
        "profit_control": control,
    }
    warnings.warn("VPS_SANITIZED_PNL=" + json.dumps(snapshot, separators=(",", ":"), default=str))
