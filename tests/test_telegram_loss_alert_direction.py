from decimal import Decimal

from learnerbot import telegram_loss_alert_direction_patch as patch


def test_loss_alert_page_uses_positive_loss_magnitude(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_ALERTS_PAGE",
        lambda app, tid: (
            "<b>🚨 LIVE POSITION LOSS ALERT</b>\n"
            "Trigger: <b>LIVE position P&amp;L ≤ -10%</b>\n"
            "<i>Profit and loss alerts are notifications only. Each user chooses their own percentages and report interval.</i>"
        ),
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts,
        "loss_alert_threshold_pct",
        lambda app, tid: Decimal("10"),
    )

    text = patch.alerts_page(object(), "123")

    assert "LIVE position loss ≥ 10%" in text
    assert "P&amp;L ≤ -10%" not in text
    assert "reaches or exceeds" in text


def test_loss_alert_keyboard_says_greater_than_or_equal(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_ALERTS_KEYBOARD",
        lambda app, tid: {
            "inline_keyboard": [[
                {"text": "🔻 -10%", "callback_data": "myalerts:set:loss"}
            ]]
        },
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts,
        "loss_alert_threshold_pct",
        lambda app, tid: Decimal("10"),
    )

    kb = patch.alerts_keyboard(object(), "123")

    assert kb["inline_keyboard"][0][0]["text"] == "🔻 ≥10% loss"
