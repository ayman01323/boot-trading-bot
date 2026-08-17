from decimal import Decimal
from types import SimpleNamespace

from learnerbot import telegram_usd_everywhere_patch as p


class Chain:
    def __init__(self, slug, name):
        self.slug = slug
        self.name = name


def test_annotates_native_and_stable_values(monkeypatch):
    chains = [Chain("bsc", "BNB Smart Chain")]
    monkeypatch.setattr(
        p,
        "_price_maps",
        lambda app: (
            {"BNB": Decimal("600"), "WBNB": Decimal("600"), "USDC": Decimal("1"), "SOL": Decimal("100")},
            {"bsc": {"BNB": Decimal("600"), "WBNB": Decimal("600"), "USDC": Decimal("1")}, "solana": {"SOL": Decimal("100")}},
            chains,
        ),
    )
    text = "<b>🌐 BSC</b>\nBalance 0.01 BNB | stable 2 USDC\nSolana reserve 0.0005 SOL"
    out = p.annotate_text(SimpleNamespace(), text)
    assert "0.01 BNB (≈ $6.00)" in out
    assert "2 USDC (≈ $2.00)" in out
    assert "0.0005 SOL (≈ $0.05)" in out


def test_chain_specific_token_price(monkeypatch):
    chains = [Chain("base", "Base")]
    monkeypatch.setattr(
        p,
        "_price_maps",
        lambda app: (
            {},
            {"base": {"TOKEN": Decimal("2.5")}},
            chains,
        ),
    )
    out = p.annotate_text(SimpleNamespace(), "<b>BASE</b> — TOKEN 4")
    # Amount-before-symbol is the Telegram convention used by the bot.
    out = p.annotate_text(SimpleNamespace(), "<b>BASE</b> — 4 TOKEN")
    assert "4 TOKEN (≈ $10.00)" in out


def test_does_not_duplicate_existing_usd(monkeypatch):
    monkeypatch.setattr(
        p,
        "_price_maps",
        lambda app: ({"SOL": Decimal("100")}, {"solana": {"SOL": Decimal("100")}}, []),
    )
    text = "Estimated capital 0.02 SOL (≈ $2.00)"
    assert p.annotate_text(SimpleNamespace(), text) == text


def test_tuple_page_keeps_keyboard(monkeypatch):
    monkeypatch.setattr(
        p,
        "_price_maps",
        lambda app: ({"ETH": Decimal("3000")}, {}, []),
    )
    kb = {"inline_keyboard": [[{"text": "Back"}]]}
    wrapped = p._wrap_text(lambda app: ("Profit 0.01 ETH", kb))
    text, returned_kb = wrapped(SimpleNamespace())
    assert "0.01 ETH (≈ $30.00)" in text
    assert returned_kb is kb
