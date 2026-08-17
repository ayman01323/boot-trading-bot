from types import SimpleNamespace

from learnerbot.telegram_wallet_links_patch import wallet_explorer_link


def test_wallet_explorer_link_uses_configured_chain_explorer():
    chain = SimpleNamespace(explorer_url="https://bscscan.com")
    wallet = "0x1111111111111111111111111111111111111111"
    text = wallet_explorer_link(chain, wallet)
    assert 'href="https://bscscan.com/address/0x1111111111111111111111111111111111111111"' in text
    assert "🔎 0x111111…111111" in text


def test_wallet_explorer_link_falls_back_when_explorer_missing():
    chain = SimpleNamespace(explorer_url="")
    wallet = "0x2222222222222222222222222222222222222222"
    text = wallet_explorer_link(chain, wallet)
    assert text == "<code>0x222222…222222</code>"
    assert "href=" not in text
