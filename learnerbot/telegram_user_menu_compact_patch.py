from __future__ import annotations

import copy
import html
from decimal import Decimal

from . import hourly_capital_alert_patch as _alerts
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from .user_registry import is_master, set_user_setting

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_PENDING = {}

_USER_ROWS = [
    [
        {"text": "🤖 SiBot", "callback_data": "menu:sibot"},
        {"text": "💰 Capital", "callback_data": "menu:capital"},
    ],
    [
        {"text": "🔐 Wallets", "callback_data": "menu:wallet"},
        {"text": "💱 Trading", "callback_data": "menu:trading"},
    ],
    [
        {"text": "⚡ Auto", "callback_data": "menu:auto"},
        {"text": "🛰 Opportunities", "callback_data": "menu:opportunities"},
    ],
    [
        {"text": "⏰ Reports & Alerts", "callback_data": "menu:myalerts"},
    ],
    [
        {"text": "📡 Status", "callback_data": "menu:status"},
        {"text": "❓ Help", "callback_data": "menu:help"},
    ],
]


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _fmt_interval(minutes: int) -> str:
    minutes = int(minutes)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{minutes} minutes"


def _fmt_pct(value) -> str:
    try:
        d = Decimal(str(value))
    except Exception:
        d = Decimal("10")
    text = format(d.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _insert_reports_button(rows):
    if any(any(b.get("callback_data") == "menu:myalerts" for b in row) for row in rows):
        return rows
    insert_at = len(rows)
    for i, row in enumerate(rows):
        if any(b.get("callback_data") in {"menu:status", "menu:help"} for b in row):
            insert_at = i
            break
    rows.insert(insert_at, [{"text": "⏰ My Reports & Loss Alerts", "callback_data": "menu:myalerts"}])
    return rows


def menu_keyboard(app=None, chat_id=None):
    """Keep the full MASTER menu, render a compact USER menu, and expose personal alerts to both."""
    if app is not None and chat_id is not None:
        try:
            if not is_master(app.csv_dir, chat_id):
                return {"inline_keyboard": copy.deepcopy(_USER_ROWS)}
        except Exception:
            # If role lookup cannot be completed, preserve the existing menu rather
            # than accidentally treating an unknown chat as an authorised USER.
            pass
    kb = copy.deepcopy(_PREV_MENU(app, chat_id))
    rows = list(kb.get("inline_keyboard") or [])
    return {"inline_keyboard": _insert_reports_button(rows)}


def alerts_page(app, tid):
    report_on = _alerts.report_enabled(app, tid)
    interval = _alerts.report_interval_minutes(app, tid)
    loss_on = _alerts.loss_alert_enabled(app, tid)
    threshold = _alerts.loss_alert_threshold_pct(app, tid)
    return "\n".join([
        "<b>⏰ MY REPORTS &amp; LOSS ALERTS</b>",
        "━━━━━━━━━━━━",
        "",
        "<b>📨 CAPITAL / GAS REPORT</b>",
        f"Automatic report: <b>{'✅ ON' if report_on else '❌ OFF'}</b>",
        f"Personal interval: <b>every {html.escape(_fmt_interval(interval))}</b>",
        "Each user has an independent schedule.",
        "",
        "<b>🚨 LIVE POSITION LOSS ALERT</b>",
        f"Loss warning: <b>{'✅ ON' if loss_on else '❌ OFF'}</b>",
        f"Trigger: <b>LIVE position P&amp;L ≤ -{html.escape(_fmt_pct(threshold))}%</b>",
        "The threshold is only a Telegram warning. It does not change your stop-loss or submit an extra SELL.",
        "",
        "<i>Both controls are optional and are stored separately for this Telegram user.</i>",
    ])


def alerts_keyboard(app, tid):
    report_on = _alerts.report_enabled(app, tid)
    interval = _alerts.report_interval_minutes(app, tid)
    loss_on = _alerts.loss_alert_enabled(app, tid)
    threshold = _alerts.loss_alert_threshold_pct(app, tid)
    return {"inline_keyboard": [
        [
            {"text": f"{'✅' if report_on else '❌'} Auto report", "callback_data": "myalerts:toggle:report"},
            {"text": f"⏱ {_fmt_interval(interval)}", "callback_data": "myalerts:set:interval"},
        ],
        [{"text": "📨 Send report now", "callback_data": "myalerts:sendnow"}],
        [
            {"text": f"{'✅' if loss_on else '❌'} Loss alert", "callback_data": "myalerts:toggle:loss"},
            {"text": f"🔻 {_fmt_pct(threshold)}%", "callback_data": "myalerts:set:loss"},
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if cqid:
        try:
            _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
        except Exception:
            pass


def _render(app, tid, cb=None):
    _sibot_ui._render(app, tid, alerts_page(app, tid), alerts_keyboard(app, tid), cb)


def _set_global(app, tid, key, value, description):
    set_user_setting(
        app.csv_dir,
        tid,
        key,
        value,
        chain_id="*",
        description=description,
    )


def _parse_interval(raw: str) -> int:
    text = str(raw or "").strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Send a time such as 30m, 1h, 2h or 120")
    multiplier = Decimal(1)
    if text.endswith("minutes"):
        text = text[:-7]
    elif text.endswith("minute"):
        text = text[:-6]
    elif text.endswith("mins"):
        text = text[:-4]
    elif text.endswith("min"):
        text = text[:-3]
    elif text.endswith("m"):
        text = text[:-1]
    elif text.endswith("hours"):
        text = text[:-5]
        multiplier = Decimal(60)
    elif text.endswith("hour"):
        text = text[:-4]
        multiplier = Decimal(60)
    elif text.endswith("hrs"):
        text = text[:-3]
        multiplier = Decimal(60)
    elif text.endswith("hr"):
        text = text[:-2]
        multiplier = Decimal(60)
    elif text.endswith("h"):
        text = text[:-1]
        multiplier = Decimal(60)
    try:
        minutes = Decimal(text) * multiplier
    except Exception as exc:
        raise ValueError("Invalid time. Examples: 30m, 1h, 2h or 120") from exc
    if minutes != minutes.to_integral_value():
        raise ValueError("The schedule must resolve to a whole number of minutes")
    value = int(minutes)
    if value < 5 or value > 1440:
        raise ValueError("Choose between 5 minutes and 24 hours")
    return value


def _parse_threshold(raw: str) -> str:
    text = str(raw or "").strip().replace("%", "")
    try:
        value = Decimal(text)
    except Exception as exc:
        raise ValueError("Send a loss percentage such as 10 or 12.5") from exc
    if value < Decimal("1") or value > Decimal("95"):
        raise ValueError("Choose a loss threshold between 1% and 95%")
    value = value.quantize(Decimal("0.01")).normalize()
    return format(value, "f")


def _prompt(app, tid, kind, cb):
    _PENDING[str(tid)] = kind
    if kind == "interval":
        text = "\n".join([
            "<b>⏱ Change your report time</b>",
            "━━━━━━━━━━━━",
            f"Current: <b>every {html.escape(_fmt_interval(_alerts.report_interval_minutes(app, tid)))}</b>",
            "Allowed: <b>5 minutes to 24 hours</b>",
            "",
            "Send <code>30m</code>, <code>1h</code>, <code>2h</code> or a number of minutes.",
            "Send <code>/cancel</code> to cancel.",
        ])
    else:
        text = "\n".join([
            "<b>🔻 Change LIVE loss alert threshold</b>",
            "━━━━━━━━━━━━",
            f"Current: <b>{html.escape(_fmt_pct(_alerts.loss_alert_threshold_pct(app, tid)))}%</b>",
            "Allowed: <b>1% to 95%</b>",
            "",
            "Send a number such as <code>10</code> or <code>12.5</code>.",
            "Send <code>/cancel</code> to cancel.",
        ])
    kb = {"inline_keyboard": [[{"text": "Cancel", "callback_data": "menu:myalerts"}]]}
    _sibot_ui._render(app, tid, text, kb, cb)


def _handle_pending(app, tid, text):
    kind = _PENDING.get(str(tid))
    if not kind:
        return False
    if text.startswith("/"):
        _PENDING.pop(str(tid), None)
        if text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/cancel":
            _ui._send(app, tid, "✅ Report/alert setting change cancelled.", alerts_keyboard(app, tid))
            return True
        return False
    try:
        if kind == "interval":
            value = _parse_interval(text)
            _set_global(
                app,
                tid,
                _alerts.REPORT_INTERVAL_KEY,
                str(value),
                "Per-user Telegram capital/gas report interval in minutes",
            )
            confirmation = f"✅ Automatic report interval set to <b>{html.escape(_fmt_interval(value))}</b>."
        else:
            value = _parse_threshold(text)
            _set_global(
                app,
                tid,
                _alerts.LOSS_ALERT_THRESHOLD_KEY,
                value,
                "Per-user LIVE position loss warning threshold percent",
            )
            confirmation = f"✅ LIVE loss warning threshold set to <b>{html.escape(value)}%</b>."
        _PENDING.pop(str(tid), None)
        _ui._send(app, tid, confirmation + "\n\n" + alerts_page(app, tid), alerts_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend another value or <code>/cancel</code>.")
    return True


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "menu:myalerts" or data.startswith("myalerts:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "menu:myalerts":
                    _PENDING.pop(str(tid), None)
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
                    # Start a fresh interval when re-enabled rather than sending an
                    # unexpected immediate report from a stale in-memory timestamp.
                    _alerts._REPORT_LAST_SENT.pop(str(tid), None)
                    _render(app, tid, cb)
                elif data == "myalerts:set:interval":
                    _prompt(app, tid, "interval", cb)
                elif data == "myalerts:sendnow":
                    _ui._send(app, tid, _alerts.scheduled_report_text(app, tid), alerts_keyboard(app, tid))
                elif data == "myalerts:toggle:loss":
                    new_value = not _alerts.loss_alert_enabled(app, tid)
                    _set_global(
                        app,
                        tid,
                        _alerts.LOSS_ALERT_ENABLED_KEY,
                        "true" if new_value else "false",
                        "Enable this user's LIVE position loss Telegram warning",
                    )
                    if not new_value:
                        _alerts._LOSS_ACTIVE = {k for k in _alerts._LOSS_ACTIVE if k[0] != str(tid)}
                    _render(app, tid, cb)
                elif data == "myalerts:set:loss":
                    _prompt(app, tid, "loss", cb)
            except Exception as exc:
                _ui._send(app, tid, f"❌ <b>Reports &amp; Alerts</b>\n<code>{html.escape(str(exc)[:360])}</code>", alerts_keyboard(app, tid))
            return

    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and _ui._auth(app, tid) and _handle_pending(app, tid, text):
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_user_menu_compact_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._user_menu_compact_patch_installed = True


install()
