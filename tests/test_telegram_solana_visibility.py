from learnerbot import telegram_solana_visibility_patch as vis


def test_solana_is_first_and_unique_in_top20_picker():
    kb = {"inline_keyboard": [
        [{"text": "BSC", "callback_data": "sibot:top20:56"}, {"text": "BASE", "callback_data": "sibot:top20:8453"}],
        [{"text": "🟣 SOLANA", "callback_data": "sibot:top20:solana"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]}
    out = vis._ensure_solana_picker(kb, "sibot:top20")
    callbacks = [b["callback_data"] for row in out["inline_keyboard"] for b in row]
    assert callbacks[0] == "sibot:top20:solana"
    assert callbacks.count("sibot:top20:solana") == 1
    assert "sibot:top20:56" in callbacks
    assert "sibot:top20:8453" in callbacks


def test_solana_is_injected_into_leader_picker():
    kb = {"inline_keyboard": [[{"text": "ETH", "callback_data": "sibot:leaders:1"}]]}
    out = vis._ensure_solana_picker(kb, "sibot:leaders")
    assert out["inline_keyboard"][0][0]["callback_data"] == "sibot:leaders:solana"
