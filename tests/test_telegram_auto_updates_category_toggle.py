from learnerbot import telegram_auto_updates_category_toggle_patch as patch


class _App:
    def __init__(self, settings):
        self._settings = dict(settings)

    def telegram_settings(self):
        return dict(self._settings)

    def general(self):
        return {"telegram_report_enabled": "true", "telegram_report_mode": "meaningful"}


def _callbacks(app):
    keyboard = patch.alerts_keyboard(app)
    return [
        button.get("callback_data")
        for row in keyboard["inline_keyboard"]
        for button in row
        if str(button.get("callback_data") or "").startswith("alerts:categories:")
    ]


def test_all_categories_on_turns_into_off_toggle():
    app = _App({key: "true" for key in patch._DEFAULTS})
    assert patch.category_state(app) == "ON"
    assert _callbacks(app) == ["alerts:categories:off"]


def test_all_categories_off_turns_into_on_toggle():
    app = _App({key: "false" for key in patch._DEFAULTS})
    assert patch.category_state(app) == "OFF"
    assert _callbacks(app) == ["alerts:categories:on"]


def test_mixed_categories_offer_both_bulk_actions():
    settings = {key: "false" for key in patch._DEFAULTS}
    settings["auto_alert_profit_increase"] = "true"
    app = _App(settings)
    assert patch.category_state(app) == "MIXED"
    assert _callbacks(app) == ["alerts:categories:on", "alerts:categories:off"]
