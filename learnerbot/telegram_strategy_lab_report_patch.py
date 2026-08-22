from __future__ import annotations

import html

from . import strategy_lab as _lab
from . import telegram as _tg
from . import telegram_ui as _ui

_ORIGINAL_HANDLE_UPDATE = _ui.handle_update
_ORIGINAL_SET_COMMANDS = _ui.set_commands

_STATUS_ICON = {
    "PROMOTION_CANDIDATE": "🟢",
    "REPLACE": "🔴",
    "REWORK": "🟠",
    "PROBATION": "🟡",
    "SHADOW": "⚪",
    "PROPOSED": "⚪",
    "RETIRED": "⚫",
}


def _fmt(v, digits=6):
    try:
        return f"{float(v):+.{digits}f}"
    except Exception:
        return str(v)


def strategy_lab_page(app) -> str:
    report = _lab.portfolio_report(app)
    totals = report.get("totals") or {}
    lines = [
        "<b>🧪 STRATEGY LAB</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        (
            f"Strategies: <b>{totals.get('strategies', 0)}</b>  •  "
            f"🟢 Promotion: <b>{totals.get('promotion_candidates', 0)}</b>  •  "
            f"🔴 Replace: <b>{totals.get('replace', 0)}</b>  •  "
            f"🟠 Rework: <b>{totals.get('rework', 0)}</b>"
        ),
        "",
        "<i>Money-weighted net profit after recorded costs, not raw trade count or win rate. Recording only — this report never arms LIVE trading, changes capital or promotes anything automatically.</i>",
        "",
    ]

    strategies = sorted(
        report.get("strategies") or [],
        key=lambda d: (
            int((d.get("metrics") or {}).get("windows") or 0) > 0,
            float((d.get("metrics") or {}).get("net_profit") or 0),
        ),
        reverse=True,
    )

    body_lines = []
    for d in strategies:
        m = d.get("metrics") or {}
        icon = _STATUS_ICON.get(str(d.get("status") or ""), "⚪")
        windows = int(m.get("windows") or 0)
        name = html.escape(str(d.get("name") or d.get("strategy_id") or ""))
        family = html.escape(str(d.get("family") or ""))
        if windows == 0:
            body_lines.append(f"{icon} <b>{name}</b>  ({family})\n   No recorded activity yet.")
            continue
        trades = int(m.get("trades") or 0)
        eligible = int(m.get("eligible_opportunities") or 0)
        net = _fmt(m.get("net_profit"))
        pf = m.get("profit_factor")
        wins = int(m.get("wins") or 0)
        losses = int(m.get("losses") or 0)
        body_lines.append(
            f"{icon} <b>{name}</b>  ({family})\n"
            f"   status=<b>{html.escape(str(d.get('status') or ''))}</b>  "
            f"net=<b>{net}</b>  pf=<b>{html.escape(str(pf))}</b>  "
            f"trades={trades}/{eligible}  wins={wins} losses={losses}"
        )

    out = "\n".join(lines + body_lines)
    if len(out) > 3800:
        out = out[:3800] + "\n\n<i>… truncated; see /strategylab &lt;name&gt; for a single strategy if needed.</i>"
    return out


def set_commands(token: str):
    _ORIGINAL_SET_COMMANDS(token)
    try:
        commands = _tg._json("getMyCommands", token, payload={}, timeout=15) or []
        existing = {str(x.get("command") or "") for x in commands}
        if "strategylab" not in existing:
            commands.append({"command": "strategylab", "description": "Strategy Lab: real per-family P&L evidence"})
            _tg._json("setMyCommands", token, payload={"commands": commands[:100]}, timeout=15)
    except Exception as exc:
        print("[strategy-lab-commands]", type(exc).__name__, exc)


def handle_update(app, update):
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd == "/strategylab":
            try:
                _ui._require_master(app, tid)
                _ui._send(app, tid, strategy_lab_page(app), _ui.back_keyboard())
            except Exception as exc:
                _ui._send(app, tid, f"❌ <b>Strategy Lab</b>\n<code>{html.escape(str(exc)[:360])}</code>", _ui.back_keyboard())
            return
    return _ORIGINAL_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_strategy_lab_report_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _ui._strategy_lab_report_patch_installed = True


install()

# Final MASTER-only operations summary wraps all prior Telegram menu/command layers.
from . import telegram_master_ai_dashboard_patch as _master_ai_dashboard  # noqa: E402,F401
