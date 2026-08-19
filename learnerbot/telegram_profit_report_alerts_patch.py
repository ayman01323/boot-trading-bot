from __future__ import annotations

import html
import time
from decimal import Decimal

from . import hourly_capital_alert_patch as _alerts
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from . import telegram_user_menu_compact_patch as _compact

PROFIT_ALERT_ENABLED_KEY = "live_profit_alert_enabled"
PROFIT_ALERT_THRESHOLD_KEY = "live_profit_alert_threshold_pct"

_PREV_PROCESS_USER = _alerts._process_user
_PREV_UI_HANDLE_UPDATE = _ui.handle_update
_PREV_HANDLE_PENDING = _compact._handle_pending
_INSTALLED = False


def profit_alert_enabled(app, tid) -> bool:
    return _alerts._bool(
        _alerts.user_setting(app.csv_dir, tid, 0, PROFIT_ALERT_ENABLED_KEY, "false"),
        False,
    )


def profit_alert_threshold_pct(app, tid) -> Decimal:
    raw = _alerts.user_setting(app.csv_dir, tid, 0, PROFIT_ALERT_THRESHOLD_KEY, "10")
    value = _alerts._dec(raw, "10")
    return min(Decimal("95"), max(Decimal("1"), value))


def _live_profit_rows(app, tid, threshold: Decimal):
    rows = []
    chain_map = {int(c.chain_id): c for c in _alerts.load_chains(app, enabled_only=False)}

    try:
        evm_positions = _alerts._sibot.position_rows(app, tid, open_only=True)
    except Exception:
        evm_positions = []
    for p in evm_positions:
        if str(p.get("mode") or "").upper() != "LIVE":
            continue
        pct = _alerts._dec(p.get("unrealised_pct"), "0")
        if pct < threshold:
            continue
        cid = int(p.get("chain_id") or 0)
        chain = chain_map.get(cid)
        name = chain.name if chain else f"chain {cid}"
        asset = p.get("symbol") or p.get("token") or "token"
        pid = str(p.get("position_id") or f"evm:{cid}:{p.get('token')}")
        rows.append({
            "key": (str(tid), "evm", pid),
            "chain": name,
            "asset": _alerts._short_asset(asset),
            "pct": pct,
            "pending": bool(int(p.get("leader_exit_pending") or 0)),
        })

    try:
        sol_positions = _alerts._sol.position_rows(app, tid, open_only=True)
    except Exception:
        sol_positions = []
    for p in sol_positions:
        if str(p.get("mode") or "").upper() != "LIVE":
            continue
        pct = _alerts._dec(p.get("unrealised_pct"), "0")
        if pct < threshold:
            continue
        mint = p.get("symbol") or p.get("mint") or "token"
        pid = str(p.get("position_id") or f"sol:{p.get('mint')}")
        rows.append({
            "key": (str(tid), "solana", pid),
            "chain": "Solana",
            "asset": _alerts._short_asset(mint),
            "pct": pct,
            "pending": bool(int(p.get("leader_exit_pending") or 0)),
        })
    return rows


def _clear_profit_active(tid) -> None:
    active = set(getattr(_alerts, "_PROFIT_ACTIVE", set()))
    _alerts._PROFIT_ACTIVE = {k for k in active if k[0] != str(tid)}


def send_new_profit_alerts(app, tid) -> int:
    """Alert once per upward threshold crossing for real LIVE positions only."""
    if not profit_alert_enabled(app, tid) or not app.telegram_bot_token:
        _clear_profit_active(tid)
        return 0

    threshold = profit_alert_threshold_pct(app, tid)
    rows = _live_profit_rows(app, tid, threshold)
    active = set(getattr(_alerts, "_PROFIT_ACTIVE", set()))
    current = {r["key"] for r in rows}
    previous = {k for k in active if k[0] == str(tid)}
    new_rows = [r for r in rows if r["key"] not in previous]

    # Re-arm only after the position falls back below the user's threshold or closes.
    _alerts._PROFIT_ACTIVE = {k for k in active if k[0] != str(tid)} | current
    if not new_rows:
        return 0

    lines = [
        f"<b>🟢 LIVE PROFIT ALERT — {threshold:g}% threshold</b>",
        "━━━━━━━━━━━━",
    ]
    for r in new_rows[:10]:
        state = " • ⏳ exit pending" if r["pending"] else ""
        lines.append(
            f"📈 <b>{html.escape(r['chain'])}</b> • <code>{html.escape(r['asset'])}</code> • "
            f"P&amp;L <b>{r['pct']:+.2f}%</b>{state}"
        )
    lines += [
        "",
        "<i>This is a Telegram warning only. It does not submit a SELL or change take-profit/stop-loss settings.</i>",
    ]
    _alerts.send_message(app.telegram_bot_token, str(tid), "\n".join(lines), parse_mode="HTML")
    return len(new_rows)


def _process_user_with_profit_alert(app, tid, now_mono):
    _PREV_PROCESS_USER(app, tid, now_mono)
    try:
        send_new_profit_alerts(app, tid)
    except Exception as exc:
        print(f"[live-profit-alert:{tid}] {type(exc).__name__}: {exc}")


def _fmt_pct(value) -> str:
    return _compact._fmt_pct(value)


def alerts_page(app, tid):
    report_on = _alerts.report_enabled(app, tid)
    interval = _alerts.report_interval_minutes(app, tid)
    profit_on = profit_alert_enabled(app, tid)
    profit_threshold = profit_alert_threshold_pct(app, tid)
    loss_on = _alerts.loss_alert_enabled(app, tid)
    loss_threshold = _alerts.loss_alert_threshold_pct(app, tid)
    return "\n".join([
        "<b>⏰ MY REPORTS &amp; ALERTS</b>",
        "━━━━━━━━━━━━",
        "",
        "<b>📨 CAPITAL / GAS REPORT</b>",
        f"Automatic report: <b>{'✅ ON' if report_on else '❌ OFF'}</b>",
        f"Personal interval: <b>every {html.escape(_compact._fmt_interval(interval))}</b>",
        "Choose a preset time or enter your own interval.",
        "",
        "<b>🟢 LIVE POSITION PROFIT ALERT</b>",
        f"Profit warning: <b>{'✅ ON' if profit_on else '❌ OFF'}</b>",
        f"Trigger: <b>LIVE position P&amp;L ≥ +{html.escape(_fmt_pct(profit_threshold))}%</b>",
        "",
        "<b>🚨 LIVE POSITION LOSS ALERT</b>",
        f"Loss warning: <b>{'✅ ON' if loss_on else '❌ OFF'}</b>",
        f"Trigger: <b>LIVE position P&amp;L ≤ -{html.escape(_fmt_pct(loss_threshold))}%</b>",
        "",
        "<i>Profit and loss alerts are notifications only. Each user chooses their own percentages and report interval.</i>",
    ])


def alerts_keyboard(app, tid):
    report_on = _alerts.report_enabled(app, tid)
    interval = _alerts.report_interval_minutes(app, tid)
    profit_on = profit_alert_enabled(app, tid)
    profit_threshold = profit_alert_threshold_pct(app, tid)
    loss_on = _alerts.loss_alert_enabled(app, tid)
    loss_threshold = _alerts.loss_alert_threshold_pct(app, tid)
    return {"inline_keyboard": [
        [
            {"text": f"{'✅' if report_on else '❌'} Auto report", "callback_data": "myalerts:toggle:report"},
            {"text": f"⏱ {_compact._fmt_interval(interval)}", "callback_data": "myalerts:set:interval"},
        ],
        [{"text": "📨 Send report now", "callback_data": "myalerts:sendnow"}],
        [
            {"text": f"{'✅' if profit_on else '❌'} Profit alert", "callback_data": "myalerts:toggle:profit"},
            {"text": f"📈 +{_fmt_pct(profit_threshold)}%", "callback_data": "myalerts:set:profit"},
        ],
        [
            {"text": f"{'✅' if loss_on else '❌'} Loss alert", "callback_data": "myalerts:toggle:loss"},
            {"text": f"🔻 -{_fmt_pct(loss_threshold)}%", "callback_data": "myalerts:set:loss"},
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _render(app, tid, cb=None):
    _sibot_ui._render(app, tid, alerts_page(app, tid), alerts_keyboard(app, tid), cb)


def _set_global(app, tid, key, value, description):
    _compact._set_global(app, tid, key, value, description)


def _interval_keyboard():
    presets = [5, 15, 30, 60, 120, 240, 360, 720, 1440]
    rows = []
    for i in range(0, len(presets), 3):
        row = []
        for minutes in presets[i:i + 3]:
            row.append({
                "text": _compact._fmt_interval(minutes),
                "callback_data": f"myalerts:interval:{minutes}",
            })
        rows.append(row)
    rows.append([{"text": "✍️ Custom time", "callback_data": "myalerts:interval:custom"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:myalerts"}])
    return {"inline_keyboard": rows}


def _threshold_keyboard(kind: str):
    presets = [3, 5, 10, 15, 20, 30, 50]
    rows = []
    for i in range(0, len(presets), 3):
        rows.append([
            {"text": f"{pct}%", "callback_data": f"myalerts:{kind}:{pct}"}
            for pct in presets[i:i + 3]
        ])
    rows.append([{"text": "✍️ Custom %", "callback_data": f"myalerts:{kind}:custom"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:myalerts"}])
    return {"inline_keyboard": rows}


def _show_interval_choices(app, tid, cb=None):
    current = _alerts.report_interval_minutes(app, tid)
    text = "\n".join([
        "<b>⏱ Choose automatic report time</b>",
        "━━━━━━━━━━━━",
        f"Current: <b>every {html.escape(_compact._fmt_interval(current))}</b>",
        "",
        "Choose a preset below, or use <b>Custom time</b> for any value from 5 minutes to 24 hours.",
    ])
    _sibot_ui._render(app, tid, text, _interval_keyboard(), cb)


def _show_threshold_choices(app, tid, kind: str, cb=None):
    if kind == "profit":
        current = profit_alert_threshold_pct(app, tid)
        title = "📈 Choose LIVE profit alert %"
        direction = "+"
    else:
        current = _alerts.loss_alert_threshold_pct(app, tid)
        title = "🔻 Choose LIVE loss alert %"
        direction = "-"
    text = "\n".join([
        f"<b>{title}</b>",
        "━━━━━━━━━━━━",
        f"Current threshold: <b>{direction}{html.escape(_fmt_pct(current))}%</b>",
        "",
        "Choose a preset below, or use <b>Custom %</b> to enter any value from 1% to 95%.",
    ])
    _sibot_ui._render(app, tid, text, _threshold_keyboard(kind), cb)


def _prompt_custom(app, tid, kind: str, cb=None):
    _compact._PENDING[str(tid)] = kind
    if kind == "interval":
        current = _alerts.report_interval_minutes(app, tid)
        text = "\n".join([
            "<b>✍️ Enter custom report time</b>",
            "━━━━━━━━━━━━",
            f"Current: <b>every {html.escape(_compact._fmt_interval(current))}</b>",
            "Send <code>45m</code>, <code>1.5h</code>, <code>3h</code> or a number of minutes.",
            "Allowed: <b>5 minutes to 24 hours</b>.",
            "Send <code>/cancel</code> to cancel.",
        ])
    elif kind == "profit":
        current = profit_alert_threshold_pct(app, tid)
        text = "\n".join([
            "<b>✍️ Enter LIVE profit alert %</b>",
            "━━━━━━━━━━━━",
            f"Current: <b>+{html.escape(_fmt_pct(current))}%</b>",
            "Send a number such as <code>7</code> or <code>12.5</code>.",
            "Allowed: <b>1% to 95%</b>.",
            "Send <code>/cancel</code> to cancel.",
        ])
    else:
        current = _alerts.loss_alert_threshold_pct(app, tid)
        text = "\n".join([
            "<b>✍️ Enter LIVE loss alert %</b>",
            "━━━━━━━━━━━━",
            f"Current: <b>-{html.escape(_fmt_pct(current))}%</b>",
            "Send a number such as <code>7</code> or <code>12.5</code>.",
            "Allowed: <b>1% to 95%</b>.",
            "Send <code>/cancel</code> to cancel.",
        ])
    kb = {"inline_keyboard": [[{"text": "Cancel", "callback_data": "menu:myalerts"}]]}
    _sibot_ui._render(app, tid, text, kb, cb)


def _set_interval(app, tid, minutes: int) -> None:
    minutes = int(minutes)
    if minutes < 5 or minutes > 1440:
        raise ValueError("Choose between 5 minutes and 24 hours")
    _set_global(
        app,
        tid,
        _alerts.REPORT_INTERVAL_KEY,
        str(minutes),
        "Per-user Telegram capital/gas report interval in minutes",
    )
    _alerts._REPORT_LAST_SENT.pop(str(tid), None)


def _set_threshold(app, tid, kind: str, value: str) -> None:
    parsed = _compact._parse_threshold(value)
    if kind == "profit":
        _set_global(
            app,
            tid,
            PROFIT_ALERT_THRESHOLD_KEY,
            parsed,
            "Per-user LIVE position profit warning threshold percent",
        )
        _clear_profit_active(tid)
    else:
        _set_global(
            app,
            tid,
            _alerts.LOSS_ALERT_THRESHOLD_KEY,
            parsed,
            "Per-user LIVE position loss warning threshold percent",
        )
        _alerts._LOSS_ACTIVE = {k for k in _alerts._LOSS_ACTIVE if k[0] != str(tid)}


def _handle_pending(app, tid, text):
    kind = _compact._PENDING.get(str(tid))
    if kind not in {"interval", "profit", "loss"}:
        return _PREV_HANDLE_PENDING(app, tid, text)
    if text.startswith("/"):
        _compact._PENDING.pop(str(tid), None)
        if text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/cancel":
            _ui._send(app, tid, "✅ Report/alert setting change cancelled.", alerts_keyboard(app, tid))
            return True
        return False
    try:
        if kind == "interval":
            minutes = _compact._parse_interval(text)
            _set_interval(app, tid, minutes)
            confirmation = f"✅ Automatic report interval set to <b>{html.escape(_compact._fmt_interval(minutes))}</b>."
        else:
            value = _compact._parse_threshold(text)
            _set_threshold(app, tid, kind, value)
            label = "profit" if kind == "profit" else "loss"
            sign = "+" if kind == "profit" else "-"
            confirmation = f"✅ LIVE {label} alert threshold set to <b>{sign}{html.escape(value)}%</b>."
        _compact._PENDING.pop(str(tid), None)
        _ui._send(app, tid, confirmation + "\n\n" + alerts_page(app, tid), alerts_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend another value or <code>/cancel</code>.")
    return True


def _answer(app, cb, text=""):
    _compact._answer(app, cb, text)


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        handled = (
            data == "menu:myalerts"
            or data.startswith("myalerts:toggle:report")
            or data.startswith("myalerts:set:interval")
            or data.startswith("myalerts:interval:")
            or data.startswith("myalerts:sendnow")
            or data.startswith("myalerts:toggle:profit")
            or data.startswith("myalerts:set:profit")
            or data.startswith("myalerts:profit:")
            or data.startswith("myalerts:toggle:loss")
            or data.startswith("myalerts:set:loss")
            or data.startswith("myalerts:loss:")
        )
        if handled:
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "menu:myalerts":
                    _compact._PENDING.pop(str(tid), None)
                    _render(app, tid, cb)
                elif data == "myalerts:toggle:report":
                    new_value = not _alerts.report_enabled(app, tid)
                    _set_global(
                        app,
                        tid,
                        _alerts.REPORT_ENABLED_KEY,
                        "true" if new_value else "false",
                        "Enable this user's scheduled Telegram capital/gas report",
                    )
                    _alerts._REPORT_LAST_SENT.pop(str(tid), None)
                    if new_value:
                        _show_interval_choices(app, tid, cb)
                    else:
                        _render(app, tid, cb)
                elif data == "myalerts:set:interval":
                    _show_interval_choices(app, tid, cb)
                elif data.startswith("myalerts:interval:"):
                    value = data.rsplit(":", 1)[-1]
                    if value == "custom":
                        _prompt_custom(app, tid, "interval", cb)
                    else:
                        _set_interval(app, tid, int(value))
                        _render(app, tid, cb)
                elif data == "myalerts:sendnow":
                    _ui._send(app, tid, _alerts.scheduled_report_text(app, tid), alerts_keyboard(app, tid))
                elif data == "myalerts:toggle:profit":
                    new_value = not profit_alert_enabled(app, tid)
                    _set_global(
                        app,
                        tid,
                        PROFIT_ALERT_ENABLED_KEY,
                        "true" if new_value else "false",
                        "Enable this user's LIVE position profit Telegram warning",
                    )
                    _clear_profit_active(tid)
                    if new_value:
                        _show_threshold_choices(app, tid, "profit", cb)
                    else:
                        _render(app, tid, cb)
                elif data == "myalerts:set:profit":
                    _show_threshold_choices(app, tid, "profit", cb)
                elif data.startswith("myalerts:profit:"):
                    value = data.rsplit(":", 1)[-1]
                    if value == "custom":
                        _prompt_custom(app, tid, "profit", cb)
                    else:
                        _set_threshold(app, tid, "profit", value)
                        _render(app, tid, cb)
                elif data == "myalerts:toggle:loss":
                    new_value = not _alerts.loss_alert_enabled(app, tid)
                    _set_global(
                        app,
                        tid,
                        _alerts.LOSS_ALERT_ENABLED_KEY,
                        "true" if new_value else "false",
                        "Enable this user's LIVE position loss Telegram warning",
                    )
                    _alerts._LOSS_ACTIVE = {k for k in _alerts._LOSS_ACTIVE if k[0] != str(tid)}
                    if new_value:
                        _show_threshold_choices(app, tid, "loss", cb)
                    else:
                        _render(app, tid, cb)
                elif data == "myalerts:set:loss":
                    _show_threshold_choices(app, tid, "loss", cb)
                elif data.startswith("myalerts:loss:"):
                    value = data.rsplit(":", 1)[-1]
                    if value == "custom":
                        _prompt_custom(app, tid, "loss", cb)
                    else:
                        _set_threshold(app, tid, "loss", value)
                        _render(app, tid, cb)
            except Exception as exc:
                _ui._send(
                    app,
                    tid,
                    f"❌ <b>Reports &amp; Alerts</b>\n<code>{html.escape(str(exc)[:360])}</code>",
                    alerts_keyboard(app, tid),
                )
            return

    return _PREV_UI_HANDLE_UPDATE(app, update)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    if not hasattr(_alerts, "_PROFIT_ACTIVE"):
        _alerts._PROFIT_ACTIVE = set()
    _alerts.PROFIT_ALERT_ENABLED_KEY = PROFIT_ALERT_ENABLED_KEY
    _alerts.PROFIT_ALERT_THRESHOLD_KEY = PROFIT_ALERT_THRESHOLD_KEY
    _alerts.profit_alert_enabled = profit_alert_enabled
    _alerts.profit_alert_threshold_pct = profit_alert_threshold_pct
    _alerts._live_profit_rows = _live_profit_rows
    _alerts.send_new_profit_alerts = send_new_profit_alerts
    _alerts._process_user = _process_user_with_profit_alert

    # Existing /menu and /reports users see the expanded page because the compact
    # module resolves these globals dynamically at render time.
    _compact.alerts_page = alerts_page
    _compact.alerts_keyboard = alerts_keyboard
    _compact._handle_pending = _handle_pending
    _ui.handle_update = handle_update

    print("[telegram-profit-alerts] configurable profit/loss thresholds + report time presets enabled")


install()
