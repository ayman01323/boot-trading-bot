from __future__ import annotations

"""SiLearn-only Telegram position-update routing.

Owner request: Learner Solana LIVE position updates must be emitted by the isolated
SiLearn Telegram bot rather than relying on the separate SiBot notification path.
This layer is reporting-only: it calls the already-composed monitor first, then
reads the resulting OPEN LIVE rows and canonical evaluation values. It never
signs, broadcasts, resizes, closes, or changes a position.
"""

import html
import time
from contextlib import closing
from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_sibot as _sol
from .user_registry import all_users

APPROVED_UTC = "2026-08-29T17:20:00Z"
APPROVED_BST = "2026-08-29T18:20:00+01:00"
SUBJECT = "Route Learner position updates through SiLearn with Dexview"
UPDATE_INTERVAL_SECONDS = 300

_PREV_MONITOR = _sol.monitor_positions


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _short(value: str) -> str:
    value = str(value or "")
    return value if len(value) <= 22 else f"{value[:10]}…{value[-8:]}"


def _active_master_ids(app) -> list[str]:
    out: list[str] = []
    try:
        rows = all_users(app.csv_dir, enabled_only=True)
    except Exception:
        rows = []
    for row in rows:
        tid = str(row.get("telegram_id") or "").strip()
        role = str(row.get("role") or "").upper().strip()
        status = str(row.get("status") or "").upper().strip()
        if tid and role == "MASTER" and status == "ACTIVE":
            out.append(tid)
    return out


def _targets(app, position: dict) -> list[str]:
    owner = str(position.get("telegram_id") or "").strip()
    ordered = ([owner] if owner else []) + _active_master_ids(app)
    seen: set[str] = set()
    out: list[str] = []
    for tid in ordered:
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _state_key(position_id: str, target_tid: str) -> str:
    return f"silearn_position_update_last:{position_id}:{target_tid}"


def _due(app, position_id: str, target_tid: str, now: int) -> bool:
    try:
        with closing(_sol.connect(app)) as conn:
            last = _sol._int(_sol._state(conn, _state_key(position_id, target_tid), "0"), 0)
        return now - last >= UPDATE_INTERVAL_SECONDS
    except Exception:
        return True


def _mark_sent(app, position_id: str, target_tid: str, now: int) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _sol._set_state(conn, _state_key(position_id, target_tid), str(int(now)))
    except Exception as exc:
        print("[silearn-position-update] mark_error", type(exc).__name__, exc)


def _position_text(app, position: dict) -> str:
    p = dict(position or {})
    mint = str(p.get("mint") or "")
    pid = str(p.get("position_id") or "")
    leader = str(p.get("leader_wallet") or "")
    cash_cost = _d(p.get("entry_cost_sol"), 0)
    peak = _d(p.get("peak_unrealised_pct"), 0)

    # Use the canonical, fully composed runtime valuation (including refundable-rent
    # accounting and all later evaluation patches) rather than inventing a parallel
    # P/L formula in the Telegram layer.
    ev = dict(_sol.evaluate_position(app, p) or {})
    current_exit = _d(ev.get("proceeds_sol"), p.get("current_exit_sol") or 0)
    net_sol = _d(ev.get("net_sol"), p.get("unrealised_net_sol") or 0)
    net_pct = _d(ev.get("net_pct"), p.get("unrealised_pct") or 0)
    rent = _d(ev.get("refundable_rent_sol"), 0)
    economic_cost = max(Decimal(0), cash_cost - rent)

    lines = [
        "🟣 <b>SiLearner — Solana LIVE POSITION UPDATE</b>",
        f"Position: <code>{html.escape(pid)}</code>",
        f"Token: <code>{html.escape(_short(mint))}</code>",
        f"Entry wallet spend: <b>{cash_cost:.9f} SOL</b>",
    ]
    if rent > 0:
        lines.extend([
            f"Refundable rent reserved: <b>{rent:.9f} SOL</b>",
            f"Economic trade cost: <b>{economic_cost:.9f} SOL</b>",
        ])
    lines.extend([
        f"Current estimated exit: <b>{current_exit:.9f} SOL</b>",
        f"Estimated net P/L: <b>{net_sol:+.9f} SOL ({net_pct:+.2f}%)</b>",
        f"Peak: <b>{peak:+.2f}%</b>",
        (
            f"Leader: <a href=\"https://solscan.io/account/{html.escape(leader, quote=True)}\">"
            f"{html.escape(_short(leader))}</a>"
        ),
        f"Dexview: <a href=\"https://www.dexview.com/solana/{html.escape(mint, quote=True)}\">Open Dexview</a>",
    ])
    return "\n".join(lines)


def send_silearn_position_updates(app, force: bool = False) -> int:
    now = int(time.time())
    try:
        with closing(_sol.connect(app)) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM positions WHERE status='OPEN' AND mode='LIVE' ORDER BY entry_ts"
            ).fetchall()]
    except Exception as exc:
        print("[silearn-position-update] db_error", type(exc).__name__, exc)
        return 0

    sent = 0
    for position in rows:
        pid = str(position.get("position_id") or "")
        mint = str(position.get("mint") or "")
        if not pid or not mint:
            continue
        try:
            text = _position_text(app, position)
        except Exception as exc:
            print("[silearn-position-update] evaluate_error", pid, type(exc).__name__, exc)
            continue
        for tid in _targets(app, position):
            if not force and not _due(app, pid, tid, now):
                continue
            try:
                # _live._notify uses app.telegram_bot_token. On the isolated Google
                # Learner this is independently verified as @SiLearn_bot.
                _live._notify(app, tid, text)
                _mark_sent(app, pid, tid, now)
                sent += 1
                print(
                    f"[silearn-position-update] sent=1 position={pid} target={tid} "
                    f"mint={mint} dexview=true leader_link=true interval={UPDATE_INTERVAL_SECONDS}s"
                )
            except Exception as exc:
                print("[silearn-position-update] send_error", tid, type(exc).__name__, exc)
    return sent


def monitor_positions_with_silearn_updates(app):
    # All pre-existing position management executes first and remains authoritative.
    result = _PREV_MONITOR(app)
    try:
        send_silearn_position_updates(app, force=False)
    except Exception as exc:
        # Reporting must never break the trading monitor.
        print("[silearn-position-update] wrapper_error", type(exc).__name__, exc)
    return result


def install() -> None:
    if getattr(_sol, "_silearn_position_updates_installed", False):
        return
    _sol.monitor_positions = monitor_positions_with_silearn_updates
    _sol.send_silearn_position_updates = send_silearn_position_updates
    _sol._silearn_position_updates_installed = True
    print(
        "[silearn-position-update] bot=SiLearn approved=2026-08-29T17:20:00Z "
        "route=position_owner+all_active_masters dexview=true leader_link=true interval=300s "
        "reporting_only=true trading_logic_unchanged=true"
    )


install()
