from learnerbot import telegram_mobile_divider_patch as mobile


def test_long_divider_is_shortened_for_mobile():
    text = "<b>Header</b>\n━━━━━━━━━━━━━━━━━━━━\nBody"
    out = mobile._mobile_text(text)
    assert out == "<b>Header</b>\n━━━━━━━━━━━━\nBody"


def test_normal_text_and_short_divider_are_unchanged():
    text = "Header\n━━━━━━━━━━━━\nnot-a-divider ━━━"
    assert mobile._mobile_text(text) == text
