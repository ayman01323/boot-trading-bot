from __future__ import annotations

import html

from . import telegram_profit_report_alerts_patch as _alerts_ui

_ORIGINAL_ALERTS_PAGE = _alerts_ui.alerts_page
_ORIGINAL_ALERTS_KEYBOARD = _alerts_ui.alerts_keyboard
_ORIGINAL_SHOW_THRESHOLD_CHOICES = _alerts_ui._show_threshold_choices
_ORIGINAL_PROMPT_CUSTOM = _alerts_ui._prompt_custom
_ORIGINAL_LIVE_LOSS_ROWS = _alerts_ui._alerts._live_loss_rows
_ORIGINAL_LIVE_PROFIT_ROWS = _alerts_ui._live_profit_rows


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


def _position_address_maps(app, tid):
    """Map alert position IDs to full copyable token/mint addresses.

    Presentation only. No position, threshold, exit or execution state is changed.
    """
    evm = {}
    solana = {}
    try:
        for p in _alerts_ui._alerts._sibot.position_rows(app, tid, open_only=True):
            cid = int(p.get("chain_id") or 0)
            token = str(p.get("token") or "").strip()
            pid = str(p.get("position_id") or f"evm:{cid}:{p.get('token')}")
            if token:
                evm[pid] = token
    except Exception:
        pass
    try:
        for p in _alerts_ui._alerts._sol.position_rows(app, tid, open_only=True):
            mint = str(p.get("mint") or "").strip()
            pid = str(p.get("position_id") or f"sol:{p.get('mint')}")
            if mint:
                solana[pid] = mint
    except Exception:
        pass
    return evm, solana


def _rows_with_full_addresses(app, tid, rows):
    evm, solana = _position_address_maps(app, tid)
    result = []
    for raw in rows:
        row = dict(raw)
        key = row.get("key") or ()
        kind = str(key[1]) if len(key) > 1 else ""
        pid = str(key[2]) if len(key) > 2 else ""
        if kind == "solana" and pid in solana:
            row["asset"] = solana[pid]
        elif kind == "evm" and pid in evm:
            row["asset"] = evm[pid]
        result.append(row)
    return result


def _live_loss_rows_full_addresses(app, tid, threshold):
    return _rows_with_full_addresses(app, tid, _ORIGINAL_LIVE_LOSS_ROWS(app, tid, threshold))


def _live_profit_rows_full_addresses(app, tid, threshold):
    return _rows_with_full_addresses(app, tid, _ORIGINAL_LIVE_PROFIT_ROWS(app, tid, threshold))


def install():
    if getattr(_alerts_ui, "_loss_alert_direction_wording_installed", False):
        return
    _alerts_ui.alerts_page = alerts_page
    _alerts_ui.alerts_keyboard = alerts_keyboard
    _alerts_ui._show_threshold_choices = _show_threshold_choices
    _alerts_ui._prompt_custom = _prompt_custom
    # Position alert messages must show full copyable addresses. The underlying
    # alert thresholds and one-shot crossing behaviour remain unchanged.
    _alerts_ui._alerts._live_loss_rows = _live_loss_rows_full_addresses
    _alerts_ui._live_profit_rows = _live_profit_rows_full_addresses
    _alerts_ui._loss_alert_direction_wording_installed = True


install()

# The AI operations slash commands are already MASTER-scoped.  Wrap the final
# role-aware inline menu too so MASTER accounts can open the reports by button.
from . import telegram_ai_reports_menu_patch  # noqa: E402,F401
