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

    buy = order_usd.annotate_order_text(
        object(),
        "🚀 <b>Solana LIVE BUY confirmed</b>\nSpent: <b>0.005 SOL</b>\nToken: <code>mint</code>",
    )
    sell = order_usd.annotate_order_text(
        object(),
        "✅ <b>Solana LIVE SELL confirmed</b>\nReceived: <b>0.006 SOL</b>\nNet on sold portion: <b>+0.001 SOL</b>",
    )

    assert "0.005 SOL (≈ $1.00)" in buy
    assert "0.006 SOL (≈ $1.20)" in sell
    assert "+0.001 SOL (≈ $0.20)" in sell


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


def test_non_order_message_is_not_changed(monkeypatch):
    monkeypatch.setattr(
        order_usd._usd,
        "_price_maps",
        lambda app: ({"SOL": Decimal("200")}, {}, []),
    )
    text = "Balance: 0.005 SOL"
    assert order_usd.annotate_order_text(object(), text) == text
