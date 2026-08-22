from __future__ import annotations

import html
import json
import sqlite3
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import ai_four_agent_health_patch as _health5
from . import strategy_lab as _lab
from . import strategy_room as _room
from . import telegram_ui as _ui
from .config import load_chains

_PREV_MENU_KEYBOARD = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update

_CHAIN_ORDER = ("solana", "bsc", "base", "ethereum", "arbitrum", "polygon")
_CHAIN_LABELS = {
    "solana": "Solana",
    "bsc": "BNB Chain",
    "base": "Base",
    "ethereum": "Ethereum",
    "arbitrum": "Arbitrum",
    "polygon": "Polygon",
}


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _version_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "version unknown"
    if raw.lower() == "legacy":
        return "legacy"
    if raw.lower().startswith("v"):
        return raw[:18]
    if raw.replace(".", "").isdigit():
        return ("v" + raw)[:18]
    return raw[:18]


def _enabled_chain_slugs(app) -> list[str]:
    enabled = {str(c.slug).lower() for c in load_chains(app, enabled_only=True)}
    # Solana is a first-class trading chain even though it has its own settings file.
    enabled.add("solana")
    ordered = [slug for slug in _CHAIN_ORDER if slug in enabled]
    for slug in sorted(enabled):
        if slug not in ordered:
            ordered.append(slug)
    return ordered


def _evm_chain_results(app, since: int) -> dict[str, dict]:
    path = Path(app.data_dir) / "sibot.sqlite3"
    out: dict[str, dict] = defaultdict(lambda: {"pnl": Decimal(0), "trades": 0, "version": "", "latest": 0})
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        cols = _columns(conn, "positions")
        version_expr = "strategy_version" if "strategy_version" in cols else "'' AS strategy_version"
        pnl_expr = "realised_user_net_native" if "realised_user_net_native" in cols else "realised_net_native"
        rows = conn.execute(
            f"""SELECT chain_slug,closed_at,{pnl_expr} AS pnl,{version_expr}
                  FROM positions
                 WHERE mode='LIVE' AND status='CLOSED' AND closed_at>=?
                 ORDER BY closed_at""",
            (int(since),),
        ).fetchall()
        conn.close()
        for raw in rows:
            row = dict(raw)
            slug = str(row.get("chain_slug") or "").lower()
            if not slug:
                continue
            item = out[slug]
            item["pnl"] += _dec(row.get("pnl"))
            item["trades"] += 1
            closed = int(row.get("closed_at") or 0)
            if closed >= int(item.get("latest") or 0):
                item["latest"] = closed
                item["version"] = str(row.get("strategy_version") or item.get("version") or "")
    except Exception:
        return out

    # Include successful direct-market AUTO outcomes without double-counting SiBot positions.
    ledger = Path(app.csv_dir) / "auto" / "trade_provenance.sqlite3"
    if ledger.exists():
        try:
            conn = sqlite3.connect(ledger, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT chain_slug,event_ts,realised_pnl,strategy_version
                     FROM trade_events
                    WHERE action='AUTO_OUTCOME' AND event_ts>=?
                      AND status IN ('SUCCESS','SUCCESS_FEE_PENDING')
                      AND TRIM(COALESCE(realised_pnl,''))<>''
                    ORDER BY event_ts""",
                (int(since),),
            ).fetchall()
            conn.close()
            for raw in rows:
                row = dict(raw)
                slug = str(row.get("chain_slug") or "").lower()
                if not slug:
                    continue
                item = out[slug]
                item["pnl"] += _dec(row.get("realised_pnl"))
                item["trades"] += 1
                event_ts = int(row.get("event_ts") or 0)
                if event_ts >= int(item.get("latest") or 0):
                    item["latest"] = event_ts
                    item["version"] = str(row.get("strategy_version") or item.get("version") or "")
        except Exception:
            pass
    return out


def _solana_result(app, since: int) -> dict:
    result = {"pnl": Decimal(0), "trades": 0, "version": "", "latest": 0}
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    if not path.exists():
        return result
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        cols = _columns(conn, "positions")
        version_expr = "strategy_version" if "strategy_version" in cols else "'' AS strategy_version"
        rows = conn.execute(
            f"""SELECT closed_at,realised_net_sol AS pnl,{version_expr}
                  FROM positions
                 WHERE mode='LIVE' AND status='CLOSED' AND closed_at>=?
                 ORDER BY closed_at""",
            (int(since),),
        ).fetchall()
        conn.close()
        for raw in rows:
            row = dict(raw)
            result["pnl"] += _dec(row.get("pnl"))
            result["trades"] += 1
            closed = int(row.get("closed_at") or 0)
            if closed >= int(result["latest"] or 0):
                result["latest"] = closed
                result["version"] = str(row.get("strategy_version") or result.get("version") or "")
    except Exception:
        pass
    return result


def strategy_chain_summary(app, *, now: int | None = None) -> list[dict]:
    now = int(now or time.time())
    since = now - 24 * 60 * 60
    results = _evm_chain_results(app, since)
    results["solana"] = _solana_result(app, since)
    out = []
    for slug in _enabled_chain_slugs(app):
        row = results.get(slug) or {"pnl": Decimal(0), "trades": 0, "version": "", "latest": 0}
        trades = int(row.get("trades") or 0)
        pnl = _dec(row.get("pnl"))
        if trades <= 0:
            icon, state = "⚪", "NO 24H DATA"
        elif pnl > 0:
            icon, state = "🟢", "PROFITABLE"
        elif pnl < 0:
            icon, state = "🔴", "LOSING"
        else:
            icon, state = "🟡", "FLAT"
        out.append({
            "slug": slug,
            "label": _CHAIN_LABELS.get(slug, slug.replace("_", " ").title()),
            "icon": icon,
            "state": state,
            "trades": trades,
            "pnl": pnl,
            "version": _version_text(row.get("version")),
        })
    return out


def _provider_health_count() -> tuple[int, int]:
    now = int(time.time())
    root = _root()
    try:
        engineering = _health5._engineering_health(root, now).get("agents") or {}
    except Exception:
        engineering = {}
    try:
        strategy = _health5._strategy_health(root, now).get("agents") or {}
    except Exception:
        strategy = {}
    try:
        factory = _room.strategy_room_agent_health(root, now).get("agents") or {}
    except Exception:
        factory = {}
    providers = ("gpt", "claude", "gemini", "deepseek", "copilot")
    healthy = 0
    for provider in providers:
        states = {
            str((engineering.get(provider) or {}).get("state") or "").upper(),
            str((strategy.get(provider) or {}).get("state") or "").upper(),
            str((factory.get(provider) or {}).get("state") or "").upper(),
        }
        if "WORKING" in states:
            healthy += 1
    return healthy, len(providers)


def _engineering_recommendation_count() -> int:
    root = _root()
    try:
        source = (_health5._health.read_text(root, "weekly/latest_source_commit.txt") or "").strip()
        if not source:
            return 0
        run = f"weekly/runs/{source}"
        value = (
            _health5._health.read_json(root, f"{run}/selected_master_decision.json")
            or _health5._health.read_json(root, f"{run}/master_decision.json")
            or {}
        )
    except Exception:
        return 0

    seen = set()
    for key in ("recommendations", "recommended_actions", "actions", "accepted_fixes", "findings"):
        rows = value.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    text = str(row.get("title") or row.get("action") or row.get("summary") or row.get("id") or "").strip()
                else:
                    text = str(row or "").strip()
                if text:
                    seen.add(text[:240])
    return len(seen)


def _shadow_experiment_count(app) -> int:
    try:
        with _lab.connect(app) as conn:
            row = conn.execute("SELECT COUNT(*) n FROM strategy_lab_registry WHERE status='SHADOW'").fetchone()
            return int(row["n"] if hasattr(row, "keys") else row[0])
    except Exception:
        return 0


def _pending_live_approvals(app, *, max_age_seconds: int = 7 * 86400) -> int:
    folder = Path(app.data_dir) / "ai_council"
    now = int(time.time())
    count = 0
    try:
        paths = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    except Exception:
        paths = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        updated = int(value.get("updated_epoch") or value.get("created_epoch") or 0)
        if not updated or now - updated > max_age_seconds:
            continue
        leader = (value.get("leaders") or {}).get("gpt") or {}
        if str(leader.get("strategy_room_action") or "").upper() != "HUMAN_APPROVAL_REQUIRED":
            continue
        approval = str(value.get("approval_status") or leader.get("approval_status") or "PENDING").upper()
        if approval not in {"APPROVED", "REJECTED", "CANCELLED", "SUPERSEDED"}:
            count += 1
    return count


def master_ai_dashboard_text(app) -> str:
    healthy, total = _provider_health_count()
    health_icon = "🟢" if healthy == total else ("🟡" if healthy else "🔴")
    recommendations = _engineering_recommendation_count()
    engineering_icon = "🟡" if recommendations else "🟢"
    shadows = _shadow_experiment_count(app)
    approvals = _pending_live_approvals(app)

    lines = [
        "<b>🎛 MASTER AI OPERATIONS</b>",
        "",
        f"<b>🤖 AI HEALTH:</b> {health_icon} {healthy}/{total}",
        f"<b>🛠 ENGINEERING:</b> {engineering_icon} {recommendations} recommendation{'s' if recommendations != 1 else ''}",
        "",
        "<b>🧠 STRATEGIES — 24H REALISED LIVE</b>",
    ]
    for row in strategy_chain_summary(app):
        suffix = f" • {row['trades']} trade{'s' if row['trades'] != 1 else ''}" if row["trades"] else ""
        lines.append(
            f"{row['icon']} <b>{html.escape(row['label'])}</b> • {html.escape(row['version'])} • {row['state']}{suffix}"
        )
    lines += [
        "",
        f"<b>🏭 FACTORY:</b> {'🟡' if shadows else '🟢'} {shadows} experiment{'s' if shadows != 1 else ''} in SHADOW",
        f"<b>🚀 LIVE CHANGES WAITING:</b> {'🟠' if approvals else '🟢'} {approvals} approval{'s' if approvals != 1 else ''}",
        "",
        "<i>LIVE promotion path: SHADOW → PROMOTION CANDIDATE → CANARY LIVE → FULL LIVE. Full LIVE remains protected by explicit approval and must not be auto-promoted from AI opinion alone.</i>",
    ]
    return "\n".join(lines)


def _dashboard_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "masterai:refresh"}],
        [{"text": "🧠 Strategy Room", "callback_data": "sr:home"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU_KEYBOARD(app, chat_id)
    rows = list(keyboard.get("inline_keyboard") or [])
    if app is not None and chat_id is not None and _ui._master(app, chat_id):
        if not any(
            str(button.get("callback_data") or "") == "masterai:home"
            for row in rows for button in row
        ):
            rows.insert(0, [{"text": "🎛 MASTER AI Dashboard", "callback_data": "masterai:home"}])
    return {"inline_keyboard": rows}


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    cb_tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if cb_tid is not None and data in {"masterai:home", "masterai:refresh"}:
        if not _ui._master(app, cb_tid):
            try:
                _ui.answer_callback_query(app.telegram_bot_token, cb.get("id"), "MASTER only")
            except Exception:
                pass
            return
        try:
            if cb.get("id"):
                _ui.answer_callback_query(app.telegram_bot_token, cb.get("id"), "Refreshing…" if data.endswith("refresh") else "")
        except Exception:
            pass
        _ui._send(app, cb_tid, master_ai_dashboard_text(app), _dashboard_keyboard())
        return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/masterdashboard", "/aidashboard"}:
            if not _ui._master(app, tid):
                _ui._send(app, tid, "MASTER only.", _ui.back_keyboard())
                return
            _ui._send(app, tid, master_ai_dashboard_text(app), _dashboard_keyboard())
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_master_ai_dashboard_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._master_ai_dashboard_installed = True


install()
