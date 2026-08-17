import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from learnerbot.solana_wallet_store import (
    SolanaWalletError,
    SolanaWalletStore,
    parse_solana_private_key,
    validate_solana_address,
)
from learnerbot import telegram_solana_wallet_patch as patch


VALID = "5sdV3Rr2CV5uLZozZWwtpTSad31Hvi9FniYUAHCfEqbw"
VALID2 = "So11111111111111111111111111111111111111112"


def _keypair_json():
    seed = bytes(range(1, 33))
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    raw = seed + public
    return json.dumps(list(raw)), raw


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


def test_private_key_parser_accepts_verified_64_byte_keypair_and_rejects_seed_phrase():
    secret, raw = _keypair_json()
    checked, address = parse_solana_private_key(secret)
    assert checked == raw
    assert validate_solana_address(address) == address
    with pytest.raises(SolanaWalletError):
        parse_solana_private_key("word " * 12)


def test_solana_signing_key_is_encrypted_and_recoverable(tmp_path: Path):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    store = SolanaWalletStore(csv_dir, data_dir)
    store._max_wallets = lambda telegram_id: 5
    secret, raw = _keypair_json()
    row = store.save_private_key("123", secret, label="LiveSOL")
    assert row["signing"] == "true"
    assert store.has_private_key("123", row["wallet_id"])
    assert store.keypair_bytes("123", row["wallet_id"]) == raw
    encrypted = (data_dir / "user_solana_wallets" / "123" / f"{row['wallet_id']}.enc.json").read_text(encoding="utf-8")
    assert secret not in encrypted
    assert json.dumps(list(raw)) not in encrypted
    store.forget("123", row["wallet_id"])
    assert not (data_dir / "user_solana_wallets" / "123" / f"{row['wallet_id']}.enc.json").exists()


def test_wallet_menu_exposes_public_and_private_solana_import():
    class Dummy:
        csv_dir = Path(".")
        data_dir = Path(".")

    # Static first row is enough to verify both actions are exposed.
    kb = patch.solwallet_keyboard(Dummy(), 999999999)
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "solwallet:add" in callbacks
    assert "solwallet:import" in callbacks
