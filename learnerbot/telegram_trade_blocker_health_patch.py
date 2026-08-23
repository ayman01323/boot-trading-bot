from __future__ import annotations

import csv
import html
import json
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import cli as _cli
from . import polygon_focus_patch as _polygon
from . import sibot as _sibot
from . import solana_trade_diagnostics_patch as _sol_diag
from . import telegram as _tg
from . import telegram_ui as _ui
from .config import load_chains, load_kv_scoped
from .telegram import send_message
from .user_registry import all_users, is_master

_PREV_APP = _cli._app
_PREV_HANDLE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _rows(path: Path):
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _epoch(value) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _age(ts: int) -> str:
    ts = _epoch(ts)
    if not ts:
        return "unknown"
    seconds = max(0, int(time.time()) - ts)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _recent(rows, hours=1):
    cutoff = int(time.time()) - max(1, int(hours)) * 3600
    return [r for r in rows if _epoch(r.get("timestamp_epoch") or r.get("ts") or r.get("updated_epoch")) >= cutoff]


def _top_reason(rows):
    reasons = [
        str(r.get("reason") or r.get("note") or "").strip()
        for r in rows
        if str(r.get("reason") or r.get("note") or "").strip()
    ]
    if not reasons:
        return ""
    return Counter(reasons).most_common(1)[0][0][:180]


def _evm_history_summary(app, tid, chain):
    leaders = len(_sibot.leader_rows(app, str(tid), int(chain.chain_id)))
    status_wallets = complete = errors = 0
    newest = 0
    dominant = ""
    try:
        with closing(_sibot.connect(app)) as conn:
            row = conn.execute(
                """SELECT COUNT(*) n,
                          SUM(CASE WHEN history_complete=1 THEN 1 ELSE 0 END) complete,
                          SUM(CASE WHEN COALESCE(error,'')<>'' THEN 1 ELSE 0 END) errors,
                          MAX(fetched_at) newest
                   FROM wallet_history_status WHERE chain_id=?""",
                (int(chain.chain_id),),
            ).fetchone()
            if row:
                status_wallets = int(row["n"] or 0)
                complete = int(row["complete"] or 0)
                errors = int(row["errors"] or 0)
                newest = int(row["newest"] or 0)
            er = conn.execute(
                """SELECT error,COUNT(*) n FROM wallet_history_status
                   WHERE chain_id=? AND COALESCE(error,'')<>''
                   GROUP BY error ORDER BY n DESC LIMIT 1""",
                (int(chain.chain_id),),
            ).fetchone()
            if er:
                dominant = str(er["error"] or "")[:180]
    except Exception as exc:
        dominant = f"{type(exc).__name__}: {str(exc)[:130]}"
    return {
        "leaders": leaders,
        "status_wallets": status_wallets,
        "complete": complete,
        "errors": errors,
        "newest": newest,
        "dominant": dominant,
    }


def _fast_market_summary(app):
    status_rows = _rows(Path(app.csv_dir) / "auto" / "fast_market_status.csv")
    status = status_rows[-1] if status_rows else {}
    sim_rows = _recent(_rows(Path(app.csv_dir) / "auto" / "auto_trade_simulations.csv"), 1)
    exec_rows = _recent(_rows(Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"), 1)
    return {
        "status": str(status.get("status") or "UNKNOWN"),
        "updated": _epoch(status.get("updated_epoch")),
        "routes": int(float(status.get("routes") or 0)),
        "merged": int(float(status.get("merged_routes") or 0)),
        "eligible": int(float(status.get("eligible") or 0)),
        "auto_events": int(float(status.get("auto_events") or 0)),
        "note": str(status.get("note") or "")[:160],
        "simulations": len(sim_rows),
        "simulation_reason": _top_reason(sim_rows),
        "executions": len(exec_rows),
    }


def _snapshot(app, tid):
    evm = {}
    for chain in load_chains(app, enabled_only=True):
        if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
            continue
        evm[chain.slug] = _evm_history_summary(app, tid, chain)
    fast = _fast_market_summary(app)
    try:
        sol = _sol_diag.activity_summary(app, str(tid), 1)
    except Exception as exc:
        sol = {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return {
        "generated_epoch": int(time.time()),
        "etherscan_configured": bool(str(getattr(app, "etherscan_api_key", "") or "").strip()),
        "polygon_focus": bool(_polygon.focus_enabled(app)),
        "platform_auto": _bool(
            load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0).get("auto_trading_enabled"),
            False,
        ),
        "platform_live": _bool(
            load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", 0).get("trading_enabled"),
            False,
        ),
        "evm": evm,
        "fast_market": fast,
        "solana": sol,
    }


def build_report(app, tid) -> str:
    s = _snapshot(app, tid)
    lines = ["<b>🧭 WHY NO TRADE — LAST HOUR</b>", "━━━━━━━━━━━━━━━━━━━━"]

    if s["etherscan_configured"]:
        lines.append("🟢 EVM history dependency: <b>Etherscan key configured</b>")
    else:
        lines.append("🔴 EVM history dependency: <b>ETHERSCAN_API_KEY MISSING</b>")
        lines.append("   SiBot cannot verify EVM leader histories until the VPS secret is configured.")

    lines += ["", "<b>EVM SIBOT LEADER FUNNEL</b>"]
    for slug, row in s["evm"].items():
        icon = "🔴" if row["errors"] and row["leaders"] == 0 else ("🟢" if row["leaders"] else "🟡")
        lines.append(
            f"{icon} {html.escape(slug.upper())}: leaders <b>{row['leaders']}</b> • "
            f"history {row['status_wallets']} • errors {row['errors']} • newest {_age(row['newest'])} ago"
        )
        if row["dominant"] and "ETHERSCAN_API_KEY" not in row["dominant"]:
            lines.append(f"   <code>{html.escape(row['dominant'][:150])}</code>")

    f = s["fast_market"]
    lines += ["", "<b>DIRECT AUTO</b>"]
    focus = "Polygon only" if s["polygon_focus"] else "all enabled chains"
    lines.append(
        f"{'🟢' if s.get('platform_live', True) else '🔴'} Platform LIVE (signing): "
        f"<b>{'ON' if s.get('platform_live', True) else 'OFF'}</b>"
    )
    lines.append(
        f"{'🟢' if s['platform_auto'] else '🔴'} Platform AUTO: "
        f"<b>{'ON' if s['platform_auto'] else 'OFF'}</b> • scope <b>{focus}</b>"
    )
    lines.append(
        f"Scanner: <b>{html.escape(f['status'])}</b> • routes {f['routes']} • merged {f['merged']} • "
        f"eligible {f['eligible']} • auto events {f['auto_events']} • updated {_age(f['updated'])} ago"
    )
    lines.append(
        f"Last hour: wallet simulations <b>{f['simulations']}</b> • execution rows <b>{f['executions']}</b>"
    )
    if f["simulation_reason"]:
        lines.append(f"Top simulation block: <code>{html.escape(f['simulation_reason'])}</code>")
    if not f["simulations"] and f["eligible"] == 0:
        lines.append("ℹ️ No route reached wallet simulation; scanner/route/profit gates are filtering upstream.")

    lines += ["", "<b>SOLANA LIVE</b>"]
    sol = s["solana"]
    if sol.get("error"):
        lines.append(f"🔴 Diagnostics unavailable: <code>{html.escape(sol['error'])}</code>")
    else:
        counts = sol.get("counts") or {}
        engine = bool(sol.get("engine_enabled"))
        live = bool(sol.get("live_enabled"))
        lines.append(
            f"{'🟢' if engine and live else '🔴'} Engine {'ON' if engine else 'OFF'} • "
            f"LIVE {'ON' if live else 'OFF'} • leaders <b>{sol.get('leaders', 0)}</b> • "
            f"selected-leader events <b>{sol.get('events', 0)}</b>"
        )
        lines.append(
            f"Decisions: BUY {counts.get('BUY',0)} • SELL {counts.get('SELL',0)} • "
            f"REJECT {counts.get('REJECT',0)} • SKIP {counts.get('SKIP',0)}"
        )
        rows = sol.get("rows") or []
        if rows:
            recent = rows[0]
            reason = str(recent.get("reason") or "accepted/processed")
            lines.append(
                f"Latest: <b>{html.escape(str(recent.get('decision') or 'UNKNOWN'))}</b> • "
                f"<code>{html.escape(reason[:180])}</code>"
            )
        elif sol.get("leaders", 0) and not sol.get("events", 0):
            lines.append("ℹ️ A leader is selected, but no fresh selected-leader swap reached the LIVE decision path.")
        elif not sol.get("leaders", 0):
            lines.append("⚠️ No qualified Solana leader is currently selected.")

    lines += [
        "",
        "<i>This report is diagnostic only. It does not weaken profit, liquidity, simulation, "
        "loss-quarantine, reserve or signing safeguards.</i>",
    ]
    return "\n".join(lines)


def _publish_startup_health(app):
    masters = [
        str(u.get("telegram_id") or "")
        for u in all_users(app.csv_dir, enabled_only=True)
        if str(u.get("role") or "").upper() == "MASTER" and str(u.get("telegram_id") or "")
    ]
    tid = masters[0] if masters else ""
    snapshot = _snapshot(app, tid) if tid else {
        "generated_epoch": int(time.time()),
        "etherscan_configured": bool(str(getattr(app, "etherscan_api_key", "") or "").strip()),
        "polygon_focus": bool(_polygon.focus_enabled(app)),
    }
    path = Path(app.data_dir) / "trade_blocker_health.json"
    safe = {
        "generated_epoch": snapshot.get("generated_epoch"),
        "etherscan_configured": snapshot.get("etherscan_configured"),
        "polygon_focus": snapshot.get("polygon_focus"),
        "platform_auto": snapshot.get("platform_auto"),
        "platform_live": snapshot.get("platform_live"),
        "evm": snapshot.get("evm", {}),
        "fast_market": snapshot.get("fast_market", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(
        "[trade-blocker-health] etherscan=%s polygon_focus=%s"
        % ("configured" if safe["etherscan_configured"] else "MISSING", safe["polygon_focus"])
    )
    if not safe["etherscan_configured"] and masters and app.telegram_bot_token:
        marker = Path(app.data_dir) / ".evm_history_dependency_warning_epoch"
        last = _epoch(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
        if int(time.time()) - last >= 12 * 3600:
            for master_tid in masters:
                try:
                    send_message(
                        app.telegram_bot_token,
                        master_tid,
                        "🔴 <b>EVM SiBot history blocked</b>\n"
                        "<code>ETHERSCAN_API_KEY</code> is missing from the VPS runtime. "
                        "EVM leader histories cannot be verified. Use <code>/whynotrade</code> for the funnel.",
                        parse_mode="HTML",
                        protect_content=True,
                    )
                except Exception:
                    pass
            marker.write_text(str(int(time.time())) + "\n", encoding="utf-8")

    _maybe_alert_platform_gate_off(app, masters, safe)


def _maybe_alert_platform_gate_off(app, masters, safe):
    """Proactively warn masters when the platform-wide LIVE/AUTO gate is OFF.

    Shared by both the base health publisher and trade_blocker_alchemy_history_patch's
    override, so the alert fires no matter which one is the active runtime entry point.
    Both platform_auto and platform_live default to a missing-key False (fail closed),
    so a genuinely unknown value is treated the same as ON here -- this must only fire
    on a confirmed OFF, never on a snapshot that could not be computed (e.g. no master
    registered yet).
    """
    platform_auto_off = safe.get("platform_auto") is False
    platform_live_off = safe.get("platform_live") is False
    if not ((platform_auto_off or platform_live_off) and masters and app.telegram_bot_token):
        return
    marker = Path(app.data_dir) / ".platform_gate_off_warning_epoch"
    marker.parent.mkdir(parents=True, exist_ok=True)
    last = _epoch(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
    if int(time.time()) - last < 12 * 3600:
        return
    off_gates = []
    if platform_live_off:
        off_gates.append("LIVE (signing)")
    if platform_auto_off:
        off_gates.append("AUTO (execution)")
    for master_tid in masters:
        try:
            send_message(
                app.telegram_bot_token,
                master_tid,
                "🔴 <b>Platform trading gate is OFF</b>\n"
                f"Currently disabled: <code>{html.escape(', '.join(off_gates))}</code>\n"
                "No live trade can execute on any chain while this is off. "
                "If this was not a deliberate pause, re-enable with "
                "<code>/platformlive on CONFIRM</code> and/or <code>/platformauto on CONFIRM</code>. "
                "Use <code>/whynotrade</code> for the full funnel.",
                parse_mode="HTML",
                protect_content=True,
            )
        except Exception:
            pass
    marker.write_text(str(int(time.time())) + "\n", encoding="utf-8")


def _app_with_trade_blocker_health():
    app = _PREV_APP()
    try:
        _publish_startup_health(app)
    except Exception as exc:
        print(f"[trade-blocker-health] ERROR {type(exc).__name__}: {exc}")
    return app


def set_commands(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        csv_dir = Path(__file__).resolve().parents[1] / "CSVbot"
        for row in all_users(csv_dir, enabled_only=False):
            if str(row.get("role") or "").upper() != "MASTER":
                continue
            tid = str(row.get("telegram_id") or "").strip()
            if not tid.lstrip("-").isdigit():
                continue
            scope = {"type": "chat", "chat_id": int(tid)}
            commands = _tg._json("getMyCommands", token, payload={"scope": scope}, timeout=15) or []
            existing = {str(x.get("command") or "").lower() for x in commands}
            if "whynotrade" not in existing:
                commands.append({"command": "whynotrade", "description": "Explain current trade blockers"})
                _tg._json("setMyCommands", token, payload={"commands": commands[:100], "scope": scope}, timeout=15)
    except Exception as exc:
        print(f"[trade-blocker-commands] {type(exc).__name__}: {exc}")


def handle_update(app, update):
    message = (update or {}).get("message") or {}
    text = str(message.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command == "/whynotrade":
        tid = str((message.get("chat") or {}).get("id") or "")
        if not tid or not is_master(app.csv_dir, tid):
            return _PREV_HANDLE(app, update)
        send_message(
            app.telegram_bot_token,
            tid,
            build_report(app, tid),
            parse_mode="HTML",
            protect_content=True,
        )
        return True
    return _PREV_HANDLE(app, update)


def install():
    if getattr(_ui, "_trade_blocker_health_patch_installed", False):
        return
    _cli._app = _app_with_trade_blocker_health
    _ui.set_commands = set_commands
    _ui.handle_update = handle_update
    _ui._trade_blocker_health_patch_installed = True


install()
