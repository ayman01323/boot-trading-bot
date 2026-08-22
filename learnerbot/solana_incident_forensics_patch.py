from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import loss_forensics_github_export as _export

_PREV_BUILD = _export.build_loss_forensics
_INCIDENT_START = int(datetime(2026, 8, 18, 0, 0, 0, tzinfo=timezone.utc).timestamp())
_BOUNDARIES = [
    (
        "aug18_early_quality_window",
        int(datetime(2026, 8, 18, 1, 4, 47, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 8, 18, 15, 33, 45, tzinfo=timezone.utc).timestamp()),
        "Claude timeline: morning Solana quality tightening until same-day relaxation",
    ),
    (
        "aug18_relaxed_to_aug21_quality_restore",
        int(datetime(2026, 8, 18, 15, 33, 45, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 8, 21, 10, 25, 26, tzinfo=timezone.utc).timestamp()),
        "Expected permissive leader-history window; zero LIVE entries here points away from the Aug-18 settings migration",
    ),
    (
        "aug21_quality_restore_to_698e284_commit",
        int(datetime(2026, 8, 21, 10, 25, 26, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 8, 21, 21, 41, 4, tzinfo=timezone.utc).timestamp()),
        "Strict leader-quality/history-complete source window before commit 698e284; deployment may lag commit time",
    ),
    (
        "post_698e284_commit",
        int(datetime(2026, 8, 21, 21, 41, 4, tzinfo=timezone.utc).timestamp()),
        2**31 - 1,
        "Source-code boundary after require_complete_history=false commit 698e284; use position git_sha for exact deployed strategy attribution when available",
    ),
]


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _anon(value: object, prefix: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return prefix + "-unknown"
    return prefix + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _short(value: object) -> str:
    raw = str(value or "")
    return raw if len(raw) <= 18 else f"{raw[:8]}…{raw[-6:]}"


def _safe_text(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,&]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+", r"\1<redacted>", text)
    text = re.sub(r"\b(sk|gh[opusr]?|github_pat)_[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
    text = re.sub(r"\s+", " ", text)
    return text[: max(40, int(limit))]


def _iso(ts: object) -> str:
    try:
        value = int(ts or 0)
    except Exception:
        value = 0
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pf(profit: Decimal, loss: Decimal) -> str:
    if loss > 0:
        return str(profit / loss)
    return "99" if profit > 0 else "0"


def _summary(rows: list[dict]) -> dict:
    closed = [r for r in rows if r.get("status") == "CLOSED"]
    vals = [_d(r.get("realised_net_sol")) for r in closed]
    wins = [v for v in vals if v > 0]
    losses = [-v for v in vals if v < 0]
    profit = sum(wins, Decimal(0))
    loss = sum(losses, Decimal(0))
    return {
        "entries": len(rows),
        "closed": len(closed),
        "open": sum(1 for r in rows if r.get("status") == "OPEN"),
        "wins": len(wins),
        "losses": len(losses),
        "gross_profit_sol": str(profit),
        "gross_loss_sol": str(loss),
        "net_sol": str(profit - loss),
        "profit_factor": _pf(profit, loss),
        "win_rate_pct": (len(wins) * 100.0 / len(closed)) if closed else 0.0,
    }


def _entry_cash(position: dict, attempt: dict | None) -> Decimal:
    attempt = attempt or {}
    try:
        delta = int(attempt.get("wallet_delta_lamports") or 0)
    except Exception:
        delta = 0
    if delta < 0:
        return Decimal(-delta) / Decimal(1_000_000_000)
    try:
        input_raw = int(attempt.get("input_raw") or 0)
    except Exception:
        input_raw = 0
    if input_raw > 0:
        return Decimal(input_raw) / Decimal(1_000_000_000)
    if str(position.get("status") or "").upper() == "OPEN":
        return _d(position.get("entry_cost_sol"))
    return Decimal(0)


def _loss_flags(row: dict) -> list[str]:
    if _d(row.get("realised_net_sol")) >= 0:
        return []
    flags: list[str] = []
    reason = str(row.get("exit_reason") or "").upper()
    if "STOP_LOSS" in reason:
        flags.append("STOP_LOSS_TRIGGERED")
    if "LEADER_EXIT" in reason:
        flags.append("FOLLOWED_LEADER_EXIT")
    if "LOSS_CAP" in reason:
        flags.append("LEADER_EXIT_LOSS_CAP")
    if "TRAIL" in reason:
        flags.append("TRAILING_EXIT")
    if "BREAK_EVEN" in reason:
        flags.append("BREAK_EVEN_EXIT")
    if "LIQUID" in reason or "EMERGENCY" in reason or "FORCE" in reason:
        flags.append("LIQUIDITY_OR_EMERGENCY_EXIT")
    if float(row.get("peak_unrealised_pct") or 0.0) > 0:
        flags.append("GAVE_BACK_PRIOR_PROFIT")
    hold = int(row.get("hold_seconds") or 0)
    if 0 < hold <= 180:
        flags.append("FAST_REVERSAL_OR_EXECUTION_COST")
    pct = row.get("realised_pct")
    if pct is not None and float(pct) <= -20.0:
        flags.append("SEVERE_PRICE_MOVE_OR_EXIT_IMPACT")
    circuit_status = str(row.get("exit_circuit_status") or "").upper()
    if circuit_status and circuit_status not in {"CLOSED", "COMPLETE", "SUCCESS", "EXECUTED"}:
        flags.append("EXIT_CIRCUIT_FRICTION")
    if str(row.get("exit_circuit_error") or ""):
        flags.append("EXIT_EXECUTION_ERROR_RECORDED")
    entry_status = str(row.get("entry_attempt_status") or "").upper()
    if entry_status and entry_status != "EXECUTED":
        flags.append("ENTRY_EXECUTION_ANOMALY")
    if not flags:
        flags.append("MARKET_MOVE_OR_LEADER_SIGNAL_LOSS")
    return sorted(set(flags))


def _position_rows(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "positions"):
        return []
    cols = _cols(conn, "positions")
    extra = [name if name in cols else f"'LEGACY_UNKNOWN' AS {name}" for name in ("strategy_engine", "strategy_version", "git_sha")]
    return [dict(r) for r in conn.execute(
        f"""SELECT position_id,telegram_id,leader_wallet,leader_rank,mint,status,
                   token_amount_raw,entry_cost_sol,entry_ts,current_exit_sol,
                   unrealised_net_sol,unrealised_pct,peak_unrealised_pct,
                   realised_net_sol,exit_reason,closed_at,updated_at,
                   leader_buy_signature,exit_signature,{','.join(extra)}
            FROM positions
            WHERE mode='LIVE'
              AND (entry_ts>=? OR COALESCE(closed_at,0)>=? OR status='OPEN')
            ORDER BY entry_ts""",
        (_INCIDENT_START, _INCIDENT_START),
    ).fetchall()]


def _attempt_map(conn: sqlite3.Connection) -> dict[tuple[str, str, str, str], dict]:
    if not _table(conn, "live_execution_attempts"):
        return {}
    out: dict[tuple[str, str, str, str], dict] = {}
    for r in conn.execute(
        """SELECT telegram_id,leader_wallet,leader_signature,mint,action,status,
                  tx_signature,input_raw,output_raw,wallet_delta_lamports,error,
                  created_at,updated_at
           FROM live_execution_attempts WHERE created_at>=? ORDER BY updated_at""",
        (_INCIDENT_START,),
    ).fetchall():
        d = dict(r)
        if str(d.get("action") or "").upper() != "BUY":
            continue
        key = (
            str(d.get("telegram_id") or ""), str(d.get("leader_wallet") or ""),
            str(d.get("leader_signature") or ""), str(d.get("mint") or ""),
        )
        out[key] = d
    return out


def _circuit_map(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _table(conn, "live_exit_circuit"):
        return {}
    out: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT position_id,status,tx_signature,error,fraction,close_reason,
                  sell_raw,opened_at,updated_at
           FROM live_exit_circuit WHERE COALESCE(updated_at,0)>=? ORDER BY updated_at""",
        (_INCIDENT_START,),
    ).fetchall():
        out[str(r["position_id"])] = dict(r)
    return out


def _decisions(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "live_decisions"):
        return []
    return [dict(r) for r in conn.execute(
        """SELECT ts,telegram_id,leader_wallet,event_action,mint,decision,reason
           FROM live_decisions WHERE ts>=? ORDER BY ts""",
        (_INCIDENT_START,),
    ).fetchall()]


def _window_name(ts: int) -> str:
    for name, start, end, _ in _BOUNDARIES:
        if start <= ts < end:
            return name
    return "outside_named_windows"


def _incident_report(app) -> dict:
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    if not path.exists():
        return {"available": False, "reason": "solana_sibot.sqlite3 missing"}
    now = int(time.time())
    conn = _ro(path)
    try:
        positions = _position_rows(conn)
        attempts = _attempt_map(conn)
        circuits = _circuit_map(conn)
        decisions = _decisions(conn)
        state = {}
        if _table(conn, "state"):
            for key in (
                "worker:discovery:last_run", "worker:discovery:last_success", "worker:discovery:last_error",
                "worker:history:last_run", "worker:history:last_success", "worker:history:last_error",
                "worker:leader:last_run", "worker:leader:last_success", "worker:leader:last_error",
                "solana_corrected_live_pnl_epoch_v2",
            ):
                row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
                value = str(row[0]) if row else ""
                state[key] = _safe_text(value, 320) if key.endswith(":last_error") else value
    finally:
        conn.close()

    sanitized = []
    for p in positions:
        key = (
            str(p.get("telegram_id") or ""), str(p.get("leader_wallet") or ""),
            str(p.get("leader_buy_signature") or ""), str(p.get("mint") or ""),
        )
        attempt = attempts.get(key, {})
        circuit = circuits.get(str(p.get("position_id") or ""), {})
        entry_cash = _entry_cash(p, attempt)
        realised = _d(p.get("realised_net_sol"))
        status = str(p.get("status") or "").upper()
        realised_pct = float(realised * Decimal(100) / entry_cash) if entry_cash > 0 and status == "CLOSED" else None
        entry_ts = int(p.get("entry_ts") or 0)
        closed_at = int(p.get("closed_at") or 0)
        row = {
            "position_id": _short(p.get("position_id")),
            "account_id": _anon(p.get("telegram_id"), "acct"),
            "leader_id": _anon(p.get("leader_wallet"), "leader"),
            "leader_rank": int(p.get("leader_rank") or 0),
            "mint": _short(p.get("mint")),
            "status": status,
            "entry_utc": _iso(entry_ts),
            "closed_utc": _iso(closed_at),
            "hold_seconds": max(0, closed_at - entry_ts) if closed_at and entry_ts else 0,
            "entry_cash_sol": str(entry_cash),
            "realised_net_sol": str(realised),
            "realised_pct": realised_pct,
            "unrealised_net_sol": str(p.get("unrealised_net_sol") or "0"),
            "unrealised_pct": float(p.get("unrealised_pct") or 0.0),
            "peak_unrealised_pct": float(p.get("peak_unrealised_pct") or 0.0),
            "exit_reason": _safe_text(p.get("exit_reason"), 180),
            "entry_attempt_status": str(attempt.get("status") or ""),
            "entry_attempt_error": _safe_text(attempt.get("error"), 240),
            "exit_circuit_status": str(circuit.get("status") or ""),
            "exit_circuit_error": _safe_text(circuit.get("error"), 240),
            "exit_circuit_reason": _safe_text(circuit.get("close_reason"), 180),
            "strategy_engine": str(p.get("strategy_engine") or "LEGACY_UNKNOWN"),
            "strategy_version": str(p.get("strategy_version") or "LEGACY_UNKNOWN"),
            "git_sha": str(p.get("git_sha") or "LEGACY_UNKNOWN"),
            "window": _window_name(entry_ts),
        }
        row["loss_flags"] = _loss_flags(row)
        sanitized.append(row)

    by_day: dict[str, list[dict]] = defaultdict(list)
    by_window: dict[str, list[dict]] = defaultdict(list)
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    by_leader: dict[str, list[dict]] = defaultdict(list)
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for r in sanitized:
        day = (r.get("entry_utc") or "unknown")[:10]
        by_day[day].append(r)
        by_window[str(r.get("window") or "unknown")].append(r)
        by_strategy[str(r.get("git_sha") or "LEGACY_UNKNOWN")].append(r)
        by_leader[str(r.get("leader_id") or "unknown")].append(r)
        by_mint[str(r.get("mint") or "unknown")].append(r)

    decision_windows = defaultdict(Counter)
    decision_reasons = defaultdict(Counter)
    for d in decisions:
        w = _window_name(int(d.get("ts") or 0))
        decision = str(d.get("decision") or "UNKNOWN").upper()
        reason = _safe_text(d.get("reason"), 220) or "<none>"
        decision_windows[w][decision] += 1
        decision_reasons[w][reason] += 1

    losses = sorted(
        [r for r in sanitized if r.get("status") == "CLOSED" and _d(r.get("realised_net_sol")) < 0],
        key=lambda r: _d(r.get("realised_net_sol")),
    )
    cause_counts = Counter(flag for r in losses for flag in r.get("loss_flags") or [])
    exit_counts = Counter(str(r.get("exit_reason") or "UNKNOWN") for r in losses)

    window_meta = {
        name: {
            "start_utc": _iso(start),
            "end_utc": _iso(min(end, now)) if end < 2**31 - 1 else _iso(now),
            "note": note,
            "pnl": _summary(by_window.get(name, [])),
            "decision_counts": dict(decision_windows.get(name, {})),
            "top_decision_reasons": dict(decision_reasons.get(name, Counter()).most_common(15)),
        }
        for name, start, end, note in _BOUNDARIES
    }

    return {
        "available": True,
        "generated_utc": _iso(now),
        "incident_start_utc": _iso(_INCIDENT_START),
        "all_live_positions": _summary(sanitized),
        "daily_pnl": {day: _summary(rows) for day, rows in sorted(by_day.items())},
        "timeline_windows": window_meta,
        "by_strategy_git_sha": {k: _summary(v) for k, v in by_strategy.items()},
        "by_leader": {k: _summary(v) for k, v in sorted(by_leader.items())},
        "by_asset_mint": {k: _summary(v) for k, v in sorted(by_mint.items())},
        "loss_exit_reason_counts": dict(exit_counts.most_common()),
        "loss_cause_flag_counts": dict(cause_counts.most_common()),
        "worst_losses": losses[:50],
        "open_positions": [r for r in sanitized if r.get("status") == "OPEN"],
        "positions": sanitized,
        "worker_latest_state": state,
        "decision_rows_seen": len(decisions),
        "privacy": "Telegram IDs and leader wallets are one-way hashed; token mints and strategy SHAs are public/operational identifiers; execution/circuit error text is credential-redacted; no signing material or secrets are included.",
        "interpretation_guard": "Loss flags are evidence labels, not proof of market causation. Exact on-chain slippage/price-impact attribution requires matching transaction quotes/receipts where retained.",
    }


def build_loss_forensics_with_incident(app, zip_path, gpt_result=None, *, hours=_export.WINDOW_HOURS):
    report = _PREV_BUILD(app, zip_path, gpt_result, hours=max(int(hours), 120))
    report["solana_aug18_incident"] = _incident_report(app)
    return report


def install() -> None:
    if getattr(_export, "_solana_aug18_incident_forensics_installed", False):
        return
    _export.build_loss_forensics = build_loss_forensics_with_incident
    _export._solana_aug18_incident_forensics_installed = True


install()
