from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .hourly_gpt_strategy_review import build_review_metrics

STATUS_BRANCH = "audit-status"
STATUS_FILE = "latest_loss_forensics.json"
WINDOW_HOURS = 12


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _pf(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def _readonly(path: Path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _table_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(name),),
        ).fetchone()
    )


def _position_report(app, cutoff: int) -> dict:
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    if not path.exists():
        return {
            "available": False,
            "closed_live": [],
            "open_live": [],
            "exit_circuits": [],
            "performance": {},
        }

    conn = _readonly(path)
    try:
        closed_rows = []
        open_rows = []
        circuits = []
        if _table_exists(conn, "positions"):
            closed_rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT position_id,mint,status,token_amount_raw,entry_cost_sol,entry_ts,
                              realised_net_sol,exit_signature,exit_reason,closed_at,updated_at
                       FROM positions
                       WHERE mode='LIVE' AND status='CLOSED' AND COALESCE(closed_at,0)>=?
                       ORDER BY closed_at DESC LIMIT 500""",
                    (int(cutoff),),
                ).fetchall()
            ]
            open_rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT position_id,mint,status,token_amount_raw,entry_cost_sol,entry_ts,
                              current_exit_sol,unrealised_net_sol,unrealised_pct,
                              peak_unrealised_pct,exit_signature,exit_reason,updated_at
                       FROM positions
                       WHERE mode='LIVE' AND status='OPEN'
                       ORDER BY updated_at DESC LIMIT 250"""
                ).fetchall()
            ]
        if _table_exists(conn, "live_exit_circuit"):
            circuits = [
                dict(r)
                for r in conn.execute(
                    """SELECT position_id,status,tx_signature,error,fraction,close_reason,
                              sell_raw,opened_at,updated_at
                       FROM live_exit_circuit
                       WHERE COALESCE(updated_at,0)>=?
                       ORDER BY updated_at DESC LIMIT 250""",
                    (int(cutoff),),
                ).fetchall()
            ]
    finally:
        conn.close()

    vals = [_d(r.get("realised_net_sol")) for r in closed_rows]
    wins = [v for v in vals if v > 0]
    losses = [-v for v in vals if v < 0]
    gross_profit = sum(wins, Decimal(0))
    gross_loss = sum(losses, Decimal(0))
    net = gross_profit - gross_loss
    pf = _pf(gross_profit, gross_loss)
    exit_reasons = Counter(str(r.get("exit_reason") or "UNKNOWN")[:180] for r in closed_rows)

    worst = sorted(
        closed_rows,
        key=lambda r: _d(r.get("realised_net_sol")),
    )[:20]
    best = sorted(
        closed_rows,
        key=lambda r: _d(r.get("realised_net_sol")),
        reverse=True,
    )[:20]

    circuit_counts = Counter(str(r.get("status") or "UNKNOWN").upper() for r in circuits)
    return {
        "available": True,
        "performance": {
            "closed_trades": len(vals),
            "wins": len(wins),
            "losses": len(losses),
            "gross_profit_sol": str(gross_profit),
            "gross_loss_sol": str(gross_loss),
            "net_sol": str(net),
            "profit_factor": str(pf),
            "profit_amount_exceeds_loss_amount": bool(gross_profit > gross_loss),
            "average_win_sol": str(gross_profit / len(wins)) if wins else "0",
            "average_loss_sol": str(gross_loss / len(losses)) if losses else "0",
            "largest_win_sol": str(max(wins)) if wins else "0",
            "largest_loss_sol": str(max(losses)) if losses else "0",
            "exit_reason_counts": dict(exit_reasons.most_common(30)),
        },
        "worst_closed_live": worst,
        "best_closed_live": best,
        "open_live": open_rows,
        "exit_circuits": circuits,
        "exit_circuit_status_counts": dict(circuit_counts),
    }


def _profit_control_report(app, cutoff: int) -> dict:
    path = Path(app.data_dir) / "profit_control_loop.sqlite3"
    if not path.exists():
        return {"available": False}
    conn = _readonly(path)
    try:
        out = {"available": True}
        if _table_exists(conn, "control_state"):
            out["state"] = {
                str(r["key"]): str(r["value"])
                for r in conn.execute("SELECT key,value FROM control_state").fetchall()
            }
        if _table_exists(conn, "control_runs"):
            out["recent_runs"] = [
                dict(r)
                for r in conn.execute(
                    """SELECT generated_at,profile,closed_trades,wins,losses,
                              gross_profit_sol,gross_loss_sol,net_sol,profit_factor,
                              profile_changed,previous_profile,gpt_status
                       FROM control_runs
                       WHERE generated_at>=?
                       ORDER BY generated_at DESC LIMIT 48""",
                    (int(cutoff),),
                ).fetchall()
            ]
        if _table_exists(conn, "strategy_registry"):
            out["strategy_registry"] = [
                dict(r)
                for r in conn.execute(
                    """SELECT profile,hours_observed,closed_trades,wins,losses,
                              gross_profit_sol,gross_loss_sol,net_sol,profit_factor,
                              successful,last_used_at,updated_at
                       FROM strategy_registry ORDER BY updated_at DESC"""
                ).fetchall()
            ]
        return out
    finally:
        conn.close()


def build_loss_forensics(app, zip_path: str | Path, gpt_result: dict | None = None, *, hours: int = WINDOW_HOURS) -> dict:
    now = int(time.time())
    cutoff = now - max(1, int(hours)) * 3600
    audit_metrics = {}
    try:
        audit_metrics = build_review_metrics(zip_path)
    except Exception as exc:
        audit_metrics = {"error": f"{type(exc).__name__}: {exc}"}

    report = {
        "schema_version": 1,
        "generated_epoch": now,
        "generated_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
        "window_hours": int(hours),
        "window_start_epoch": cutoff,
        "source_audit_zip": Path(zip_path).name,
        "privacy": (
            "Sanitized operational report only. Telegram IDs, user wallet addresses, "
            "leader-wallet addresses, private keys, seed phrases, encrypted signing material, "
            "RPC credentials and passwords are excluded. Token mints and transaction signatures "
            "are public blockchain identifiers and may be included."
        ),
        "audit_metrics": audit_metrics,
        "solana_live": _position_report(app, cutoff),
        "profit_control": _profit_control_report(app, cutoff),
    }
    if gpt_result:
        report["server_gpt_review"] = {
            "ok": bool(gpt_result.get("ok")),
            "mode": str(gpt_result.get("mode") or ""),
            "review": gpt_result.get("review"),
            "error": str(gpt_result.get("error") or "")[:1200],
        }
    return report


def _run(root: Path, argv: list[str], *, input_text: str | None = None, timeout: int = 30):
    p = subprocess.run(
        argv,
        cwd=root,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
    return p.returncode, output


def _publish_git(app, report: dict) -> dict:
    root = Path(app.root)
    text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"

    rc, blob = _run(root, ["git", "hash-object", "-w", "--stdin"], input_text=text, timeout=20)
    if rc != 0:
        return {"ok": False, "error": "hash-object: " + blob[:500]}
    blob = blob.strip()

    rc, tree = _run(
        root,
        ["git", "mktree"],
        input_text=f"100644 blob {blob}\t{STATUS_FILE}\n",
        timeout=20,
    )
    if rc != 0:
        return {"ok": False, "error": "mktree: " + tree[:500]}
    tree = tree.strip()

    rc, remote = _run(
        root,
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{STATUS_BRANCH}"],
        timeout=30,
    )
    parent = ""
    if rc == 0 and remote.strip():
        parent = remote.split()[0].strip()

    args = ["git", "commit-tree", tree, "-m", f"BOOT sanitized loss forensics {report.get('generated_epoch')}"]
    if parent:
        args.extend(["-p", parent])
    rc, commit = _run(root, args, timeout=20)
    if rc != 0:
        return {"ok": False, "error": "commit-tree: " + commit[:500]}
    commit = commit.strip()

    rc, pushed = _run(
        root,
        ["git", "push", "origin", f"{commit}:refs/heads/{STATUS_BRANCH}"],
        timeout=60,
    )
    if rc != 0:
        return {"ok": False, "error": "push: " + pushed[:800]}
    return {"ok": True, "branch": STATUS_BRANCH, "file": STATUS_FILE, "commit": commit}


def publish_loss_forensics(app, zip_path: str | Path, gpt_result: dict | None = None) -> dict:
    """Build and best-effort publish a sanitized report without affecting trading."""
    try:
        report = build_loss_forensics(app, zip_path, gpt_result)
        result = _publish_git(app, report)
        return {**result, "report": report}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
