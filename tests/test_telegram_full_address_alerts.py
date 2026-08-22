from learnerbot import telegram_loss_alert_direction_patch as patch


def test_solana_loss_alert_rows_use_full_mint_address(monkeypatch):
    full_mint = "8fipYA8k1111111111111111111111111111113M3fPV"
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_LIVE_LOSS_ROWS",
        lambda app, tid, threshold: [
            {
                "key": (str(tid), "solana", "sol-pos-1"),
                "chain": "Solana",
                "asset": "8fipYA8k…3M3fPV",
                "pct": -105.82,
                "pending": True,
            }
        ],
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts._sol,
        "position_rows",
        lambda app, tid, open_only=True: [
            {"position_id": "sol-pos-1", "mint": full_mint, "mode": "LIVE"}
        ],
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts._sibot,
        "position_rows",
        lambda app, tid, open_only=True: [],
    )

    rows = patch._live_loss_rows_full_addresses(object(), "123", 10)

    assert rows[0]["asset"] == full_mint
    assert "…" not in rows[0]["asset"]


def test_evm_profit_alert_rows_prefer_full_token_contract(monkeypatch):
    full_token = "0x1234567890abcdef1234567890abcdef12345678"
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_LIVE_PROFIT_ROWS",
        lambda app, tid, threshold: [
            {
                "key": (str(tid), "evm", "evm-pos-1"),
                "chain": "Base",
                "asset": "TOKEN",
                "pct": 12.5,
                "pending": False,
            }
        ],
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts._sibot,
        "position_rows",
        lambda app, tid, open_only=True: [
            {
                "position_id": "evm-pos-1",
                "chain_id": 8453,
                "token": full_token,
                "symbol": "TOKEN",
                "mode": "LIVE",
            }
        ],
    )
    monkeypatch.setattr(
        patch._alerts_ui._alerts._sol,
        "position_rows",
        lambda app, tid, open_only=True: [],
    )

    rows = patch._live_profit_rows_full_addresses(object(), "123", 10)

    assert rows[0]["asset"] == full_token
    assert "…" not in rows[0]["asset"]
