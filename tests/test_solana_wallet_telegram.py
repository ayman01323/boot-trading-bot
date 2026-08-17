from pathlib import Path

import pytest

from learnerbot.solana_wallet_store import SolanaWalletError, SolanaWalletStore, validate_solana_address
from learnerbot import telegram_solana_wallet_patch as patch


VALID = "5sdV3Rr2CV5uLZozZWwtpTSad31Hvi9FniYUAHCfEqbw"
VALID2 = "So11111111111111111111111111111111111111112"


def test_solana_address_validation_rejects_evm_and_accepts_pubkey():
    assert validate_solana_address(VALID) == VALID
    with pytest.raises(SolanaWalletError):
        validate_solana_address("0xC9271cB571233987b7c447d8588596B978fB293D")


def test_solana_wallet_store_add_select_remove(tmp_path: Path):
    store = SolanaWalletStore(tmp_path)
    store._max_wallets = lambda telegram_id: 5
    first = store.add("123", VALID, "MainSOL")
    second = store.add("123", VALID2, "BackupSOL")
    assert first["active"] == "true"
    assert second["active"] == "false"
    assert store.get_meta("123")["address"] == VALID
    store.set_active("123", second["wallet_id"])
    assert store.get_meta("123")["address"] == VALID2
    store.forget("123", second["wallet_id"])
    assert store.get_meta("123")["address"] == VALID


def test_wallet_menu_exposes_solana_manager():
    kb = patch.wallet_keyboard()
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "solwallet:open" in callbacks
