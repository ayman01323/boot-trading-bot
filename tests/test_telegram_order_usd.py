from decimal import Decimal

from learnerbot import solana_sell_pnl_emoji_patch as sell_pnl
from learnerbot import telegram_order_usd_patch as order_usd


def test_solana_buy_and_sell_notifications_show_usd(monkeypatch):
    monkeypatch.setattr(
        order_usd._usd,
        "_price_maps",
        lambda app: (
            {"SOL": Decimal("200")},
            {"solana": {"SOL": Decimal("200")}},
            [],
        ),
    )

    mint = "6Mfj9t4WMN1c6CMpYZm8GvJsW3TiE7SrbrBqpBRnmUgd"
    buy = order_usd.annotate_order_text(
        object(),
        f"🚀 <b>Solana LIVE BUY confirmed</b>\nSpent: <b>0.005 SOL</b>\nToken: <code>{mint}</code>",
    )
    sell = order_usd.annotate_order_text(
        object(),
        "✅ <b>Solana LIVE SELL confirmed</b>\nReceived: <b>0.006 SOL</b>\nNet on sold portion: <b>+0.001 SOL</b>",
    )

    assert "0.005 SOL (≈ $1.00)" in buy
    assert f'https://www.dexview.com/solana/{mint}' in buy
    assert '>DEX View</a>' in buy
    assert "0.006 SOL (≈ $1.20)" in sell
    assert "+0.001 SOL (≈ $0.20)" in sell
    assert "dexview.com/solana/" not in sell


def test_learner_buy_notification_gets_dynamic_dexview_link(monkeypatch):
    monkeypatch.setattr(order_usd._usd, "annotate_text", lambda app, text: text)
    mint = "6Mfj9t4WMN1c6CMpYZm8GvJsW3TiE7SrbrBqpBRnmUgd"
    text = order_usd.annotate_order_text(
        object(),
        f"🚀 <b>LEARNER LIVE BUY CONFIRMED</b>\nToken: <code>{mint}</code>",
    )
    assert text.count("dexview.com/solana/") == 1
    assert f'https://www.dexview.com/solana/{mint}' in text

    already_linked = order_usd.annotate_order_text(object(), text)
    assert already_linked.count("dexview.com/solana/") == 1


def test_solana_sell_pnl_emojis_follow_realised_result():
    profit = sell_pnl.decorate_sell_pnl(
        "✅ <b>Solana LIVE SELL confirmed</b>\nNet on sold portion: <b>+0.001000000 SOL</b>"
    )
    loss = sell_pnl.decorate_sell_pnl(
        "✅ <b>Solana LIVE SELL confirmed</b>\nNet on sold portion: <b>-0.001000000 SOL</b>"
    )
    break_even = sell_pnl.decorate_sell_pnl(
        "✅ <b>Solana LIVE SELL confirmed</b>\nNet on sold portion: <b>+0.000000000 SOL</b>"
    )

    assert "Realised net P&L: 💚 <b>+0.001000000 SOL</b>" in profit
    assert "Realised net P&L: ❤️ <b>-0.001000000 SOL</b>" in loss
    assert "Realised net P&L: 🍉 <b>+0.000000000 SOL</b>" in break_even


def test_solana_sell_pnl_near_zero_matches_displayed_break_even():
    text = sell_pnl.decorate_sell_pnl(
        "✅ <b>Solana LIVE SELL confirmed</b>\nNet on sold portion: <b>+0.0000000004 SOL</b>"
    )
    assert "🍉" in text


def test_evm_buy_and_exit_notifications_show_usd(monkeypatch):
    monkeypatch.setattr(
        order_usd._usd,
        "_price_maps",
        lambda app: (
            {"ETH": Decimal("4000")},
            {},
            [],
        ),
    )

    buy = order_usd.annotate_order_text(
        object(),
        "🤖 <b>SiBot LIVE BUY</b>\nAllocation: <b>0.002 ETH</b>",
    )
    exit_text = order_usd.annotate_order_text(
        object(),
        "🤖 <b>SiBot LIVE EXIT</b>\nNet: <b>+0.0005 ETH</b>",
    )

    assert "0.002 ETH (≈ $8.00)" in buy
    assert "+0.0005 ETH (≈ $2.00)" in exit_text
    assert "dexview.com/solana/" not in buy


def test_non_order_message_is_not_changed(monkeypatch):
    monkeypatch.setattr(
        order_usd._usd,
        "_price_maps",
        lambda app: ({"SOL": Decimal("200")}, {}, []),
    )
    text = "Balance: 0.005 SOL"
    assert order_usd.annotate_order_text(object(), text) == text
