from __future__ import annotations

import html
import time
from collections import Counter
from contextlib import closing

from . import solana_sibot as _sol
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_solana_live_patch as _liveui

_PREV_PROCESS = _sol.process_leader_event
_PREV_PAGE = _liveui.solana_page


def _ensure_table(app):
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS live_decisions(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ts INTEGER NOT NULL,
                 telegram_id TEXT,
                 leader_wallet TEXT NOT NULL,
                 signature TEXT,
                 event_action TEXT NOT NULL,
                 mint TEXT NOT NULL,
                 decision TEXT NOT NULL,
                 reason TEXT
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sol_live_decisions_tid_ts ON live_decisions(telegram_id,ts)")
        conn.commit()


def _record(app, event: dict, action: dict):
    _ensure_table(app)
    tid = str(action.get("telegram_id") or "")
    decision = str(action.get("action") or "UNKNOWN").upper()
    reason = str(action.get("reason") or "")[:500]
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        conn.execute(
            """INSERT INTO live_decisions(ts,telegram_id,leader_wallet,signature,event_action,mint,decision,reason)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                int(time.time()), tid, str(event.get("leader_wallet") or ""),
                str(event.get("signature") or ""), str(event.get("action") or "").upper(),
                str(event.get("mint") or ""), decision, reason,
            ),
        )
        conn.execute("DELETE FROM live_decisions WHERE ts < ?", (int(time.time()) - 14 * 86400,))
        conn.commit()
    print(
        "[solana-live-decision] tid=%s event=%s decision=%s reason=%s mint=%s"
        % (tid or "-", str(event.get("action") or "-"), decision, reason or "-", str(event.get("mint") or "-")[:16])
    )


def process_leader_event(app, event: dict):
    actions = _PREV_PROCESS(app, event)
    for action in actions or []:
        try:
            _record(app, event, action)
        except Exception as exc:
            print("[solana-live-diagnostics]", type(exc).__name__, exc)
    return actions


def _state_snapshot(conn, worker):
    def integer(key):
        try:
            return int(_sol._state(conn, key, 0) or 0)
        except Exception:
            return 0
    return {
        "last_run": integer(f"worker:{worker}:last_run"),
        "last_success": integer(f"worker:{worker}:last_success"),
        "last_error": str(_sol._state(conn, f"worker:{worker}:last_error", "") or ""),
    }


def activity_summary(app, tid, hours=24):
    _ensure_table(app)
    since = int(time.time()) - max(1, int(hours)) * 3600
    with closing(_sol.connect(app)) as conn:
        leaders = int(conn.execute(
            "SELECT COUNT(*) n FROM leaders WHERE telegram_id=?", (str(tid),)
        ).fetchone()["n"])
        events = int(conn.execute(
            """SELECT COUNT(*) n FROM leader_events e
               WHERE e.created_at>=?
                 AND EXISTS(
                   SELECT 1 FROM leaders l
                   WHERE l.telegram_id=? AND l.wallet=e.leader_wallet
                 )""",
            (since, str(tid)),
        ).fetchone()["n"])
        last_event = int(conn.execute(
            """SELECT COALESCE(MAX(e.created_at),0) ts FROM leader_events e
               WHERE EXISTS(
                   SELECT 1 FROM leaders l
                   WHERE l.telegram_id=? AND l.wallet=e.leader_wallet
               )""",
            (str(tid),),
        ).fetchone()["ts"] or 0)
        open_positions = [dict(r) for r in conn.execute(
            """SELECT position_id,leader_wallet,mint,entry_cost_sol,entry_ts,unrealised_pct,leader_exit_pending
               FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE' ORDER BY entry_ts""",
            (str(tid),),
        ).fetchall()]
        rows = [dict(r) for r in conn.execute(
            """SELECT ts,event_action,decision,reason,mint FROM live_decisions
               WHERE telegram_id=? AND ts>=? ORDER BY ts DESC LIMIT 100""",
            (str(tid), since),
        ).fetchall()]
        workers = {name: _state_snapshot(conn, name) for name in ("discovery", "history", "leader")}
        history_errors = int(conn.execute(
            "SELECT COUNT(*) n FROM history_status WHERE TRIM(COALESCE(error,''))<>''"
        ).fetchone()["n"])
    counts = Counter(str(r.get("decision") or "UNKNOWN") for r in rows)
    try:
        engine_enabled = _sol._sibot._bool(
            _sol._sibot.user_settings(app, tid, 0).get("enabled"), False
        )
    except Exception:
        engine_enabled = False
    try:
        live_enabled = bool(_liveui.live_enabled(app, tid))
    except Exception:
        live_enabled = False
    return {
        "leaders": leaders,
        "events": events,
        "last_event": last_event,
        "open_positions": open_positions,
        "rows": rows,
        "counts": counts,
        "workers": workers,
        "history_errors": history_errors,
        "engine_enabled": engine_enabled,
        "live_enabled": live_enabled,
    }


def _reason_text(row):
    d = html.escape(str(row.get("decision") or "UNKNOWN"))
    reason = html.escape(str(row.get("reason") or "").strip())
    if not reason:
        reason = "accepted/processed"
    return f"{d}: {reason[:140]}"


def _short(v):
    v = str(v or "")
    return v if len(v) <= 18 else f"{v[:8]}…{v[-6:]}"


def _age(ts):
    if not ts:
        return "not yet"
    seconds = max(0, int(time.time()) - int(ts))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def solana_page(app, tid):
    base = _PREV_PAGE(app, tid)
    try:
        s = activity_summary(app, tid, 24)
        counts = s["counts"]
        decisions = sum(counts.values())
        cfg = _sol.settings(app)
        max_positions = max(1, min(5, _sol._int(cfg.get("live_max_positions"), 1)))
        discovery_age = max(0, int(time.time()) - int(s["workers"]["discovery"]["last_success"] or 0)) if s["workers"]["discovery"]["last_success"] else None
        leader_age = max(0, int(time.time()) - int(s["workers"]["leader"]["last_success"] or 0)) if s["workers"]["leader"]["last_success"] else None
        lines = [
            "",
            "<b>🧭 SOLANA ACTIVITY — LAST 24H</b>",
            f"Engine: {'🟢 SiBot ON' if s['engine_enabled'] else '🔴 SiBot OFF'}  •  {'🟢 LIVE ON' if s['live_enabled'] else '🔴 LIVE OFF'}",
            "Workers: "
            f"discovery <b>{html.escape(_age(s['workers']['discovery']['last_success']))}</b>  •  "
            f"leader <b>{html.escape(_age(s['workers']['leader']['last_success']))}</b>  •  "
            f"history <b>{html.escape(_age(s['workers']['history']['last_success']))}</b>",
            f"Selected leaders: <b>{s['leaders']}</b>  •  observed selected-leader events: <b>{s['events']}</b>",
            f"Last selected-leader event: <b>{html.escape(_age(s['last_event']))}</b>",
            f"Open LIVE positions: <b>{len(s['open_positions'])}/{max_positions}</b>",
            f"LIVE decisions recorded: <b>{decisions}</b>  •  BUY <b>{counts.get('BUY',0)}</b>  •  SELL <b>{counts.get('SELL',0)}</b>  •  REJECT <b>{counts.get('REJECT',0)}</b>  •  SKIP <b>{counts.get('SKIP',0)}</b>",
        ]
        if not s["engine_enabled"]:
            lines.append("⚠️ Main SiBot engine is OFF for this account; Solana LIVE cannot receive new copied entries.")
        if not s["live_enabled"]:
            lines.append("⚠️ Solana LIVE is OFF for this account.")
        discovery_limit = max(60, _sol._int(cfg.get("discovery_interval_seconds"), 15) * 6)
        leader_limit = max(30, _sol._int(cfg.get("leader_poll_seconds"), 5) * 8)
        if discovery_age is not None and discovery_age > discovery_limit:
            lines.append("⚠️ Solana discovery heartbeat is stale.")
        if leader_age is not None and leader_age > leader_limit:
            lines.append("⚠️ Solana leader/position heartbeat is stale.")
        for worker in ("discovery", "leader", "history"):
            error = str(s["workers"][worker].get("last_error") or "").strip()
            if error:
                lines.append(f"⚠️ {worker} last error: <code>{html.escape(error[:180])}</code>")
        if s["history_errors"]:
            lines.append(f"ℹ️ Wallet histories currently carrying an error: <b>{s['history_errors']}</b>")
        if s["open_positions"]:
            lines.append("<b>Current open LIVE mints</b>")
            for p in s["open_positions"][:5]:
                pending = " • leader exit pending" if int(p.get("leader_exit_pending") or 0) else ""
                lines.append(
                    f"• <code>{html.escape(_short(p.get('mint')))}</code> • entry <b>{html.escape(str(p.get('entry_cost_sol') or '0'))} SOL</b> • P&amp;L <b>{float(p.get('unrealised_pct') or 0):+.2f}%</b>{pending}"
                )
        if s["leaders"] == 0:
            lines.append("⚠️ No selected Solana leaders: ranking/quality selection is the current bottleneck.")
        elif s["events"] == 0:
            lines.append("ℹ️ Leaders are selected, but no fresh classified swap from those selected leaders was observed in this window.")
        recent = s["rows"][:5]
        if recent:
            lines.append("<b>Recent decisions</b>")
            lines.extend("• " + _reason_text(r) for r in recent)
        else:
            lines.append("No LIVE entry/exit decision has been recorded in the last 24 hours.")
        return base.rstrip() + "\n" + "\n".join(lines)
    except Exception as exc:
        return base.rstrip() + f"\n\n<b>🧭 SOLANA ACTIVITY</b>\n⚠️ diagnostics unavailable: <code>{html.escape(type(exc).__name__)}</code>"


def install():
    if getattr(_sol, "_trade_diagnostics_patch_installed", False):
        return
    _sol.process_leader_event = process_leader_event
    _liveui.solana_page = solana_page
    _intel.solana_page = solana_page
    _sol._trade_diagnostics_patch_installed = True


install()
