from __future__ import annotations

from . import telegram_ui as _ui

_PREV_ALERTS_KEYBOARD = _ui.alerts_keyboard

_DEFAULTS = {
    "auto_alert_profit_increase": True,
    "auto_alert_new_strategy": True,
    "auto_alert_strategy_upgrade": True,
    "auto_alert_new_wallet": False,
    "auto_alert_chain_config": True,
    "auto_alert_behaviour_leader": False,
}


def category_state(app) -> str:
    settings = app.telegram_settings()
    states = [
        _ui._is_on(settings.get(key, "true" if default else "false"), default)
        for key, default in _DEFAULTS.items()
        if key in _ui._ALERT_DESCRIPTIONS
    ]
    if states and all(states):
        return "ON"
    if not any(states):
        return "OFF"
    return "MIXED"


def alerts_keyboard(app):
    keyboard = _PREV_ALERTS_KEYBOARD(app)
    rows = [list(row) for row in (keyboard.get("inline_keyboard") or [])]
    state = category_state(app)

    if state == "ON":
        replacement = [{
            "text": "🟢 All Categories: ON • Tap to turn OFF",
            "callback_data": "alerts:categories:off",
        }]
    elif state == "OFF":
        replacement = [{
            "text": "🔴 All Categories: OFF • Tap to turn ON",
            "callback_data": "alerts:categories:on",
        }]
    else:
        replacement = [
            {"text": "✅ Enable All", "callback_data": "alerts:categories:on"},
            {"text": "❌ Disable All", "callback_data": "alerts:categories:off"},
        ]

    for index, row in enumerate(rows):
        if any(str(button.get("callback_data") or "").startswith("alerts:categories:") for button in row):
            rows[index] = replacement
            break
    else:
        rows.insert(max(0, len(rows) - 1), replacement)

    return {**keyboard, "inline_keyboard": rows}


def install():
    if getattr(_ui, "_auto_updates_category_toggle_patch_installed", False):
        return
    _ui.alerts_keyboard = alerts_keyboard
    _ui._auto_updates_category_toggle_patch_installed = True


install()
