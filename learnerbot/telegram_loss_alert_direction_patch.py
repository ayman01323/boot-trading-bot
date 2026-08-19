from __future__ import annotations

import html

from . import telegram_profit_report_alerts_patch as _alerts_ui

_ORIGINAL_ALERTS_PAGE = _alerts_ui.alerts_page
_ORIGINAL_ALERTS_KEYBOARD = _alerts_ui.alerts_keyboard
_ORIGINAL_SHOW_THRESHOLD_CHOICES = _alerts_ui._show_threshold_choices
_ORIGINAL_PROMPT_CUSTOM = _alerts_ui._prompt_custom


def alerts_page(app, tid):
    """Present the loss threshold as a positive loss magnitude.

    Trading/reporting logic already treats a 10% threshold as firing at a loss of
    10% or more (signed P&L <= -10%).  This patch removes the confusing signed
    comparison from the Telegram wording without changing any trading behaviour.
    """
    text = _ORIGINAL_ALERTS_PAGE(app, tid)
    threshold = _alerts_ui._alerts.loss_alert_threshold_pct(app, tid)
    old = (
        f"Trigger: <b>LIVE position P&amp;L ≤ -"
        f"{html.escape(_alerts_ui._fmt_pct(threshold))}%</b>"
    )
    new = (
        f"Trigger: <b>LIVE position loss ≥ "
        f"{html.escape(_alerts_ui._fmt_pct(threshold))}%</b>"
    )
    text = text.replace(old, new)
    text = text.replace(
        "<i>Profit and loss alerts are notifications only. Each user chooses their own percentages and report interval.</i>",
        "<i>Loss alerts fire when the loss reaches or exceeds the chosen percentage. Alerts are notifications only and do not change stop-loss settings.</i>",
    )
    return text


def alerts_keyboard(app, tid):
    kb = _ORIGINAL_ALERTS_KEYBOARD(app, tid)
    threshold = _alerts_ui._alerts.loss_alert_threshold_pct(app, tid)
    for row in kb.get("inline_keyboard", []):
        for button in row:
            if button.get("callback_data") == "myalerts:set:loss":
                button["text"] = f"🔻 ≥{_alerts_ui._fmt_pct(threshold)}% loss"
    return kb


def _show_threshold_choices(app, tid, kind: str, cb=None):
    if kind != "loss":
        return _ORIGINAL_SHOW_THRESHOLD_CHOICES(app, tid, kind, cb)

    current = _alerts_ui._alerts.loss_alert_threshold_pct(app, tid)
    text = "\n".join([
        "<b>🔻 Choose LIVE loss alert %</b>",
        "━━━━━━━━━━━━",
        f"Current loss threshold: <b>{html.escape(_alerts_ui._fmt_pct(current))}%</b>",
        "",
        "The alert fires when the LIVE position loss reaches or exceeds this percentage.",
        "Choose a preset below, or use <b>Custom %</b> to enter any value from 1% to 95%.",
    ])
    _alerts_ui._sibot_ui._render(
        app,
        tid,
        text,
        _alerts_ui._threshold_keyboard(kind),
        cb,
    )


def _prompt_custom(app, tid, kind: str, cb=None):
    if kind != "loss":
        return _ORIGINAL_PROMPT_CUSTOM(app, tid, kind, cb)

    _alerts_ui._compact._PENDING[str(tid)] = kind
    current = _alerts_ui._alerts.loss_alert_threshold_pct(app, tid)
    text = "\n".join([
        "<b>✍️ Enter LIVE loss alert %</b>",
        "━━━━━━━━━━━━",
        f"Current loss threshold: <b>{html.escape(_alerts_ui._fmt_pct(current))}%</b>",
        "Alert when loss is <b>greater than or equal to</b> this percentage.",
        "Send a number such as <code>7</code> or <code>12.5</code>.",
        "Allowed: <b>1% to 95%</b>.",
        "Send <code>/cancel</code> to cancel.",
    ])
    kb = {"inline_keyboard": [[{"text": "Cancel", "callback_data": "menu:myalerts"}]]}
    _alerts_ui._sibot_ui._render(app, tid, text, kb, cb)


def install():
    if getattr(_alerts_ui, "_loss_alert_direction_wording_installed", False):
        return
    _alerts_ui.alerts_page = alerts_page
    _alerts_ui.alerts_keyboard = alerts_keyboard
    _alerts_ui._show_threshold_choices = _show_threshold_choices
    _alerts_ui._prompt_custom = _prompt_custom
    _alerts_ui._loss_alert_direction_wording_installed = True


install()
