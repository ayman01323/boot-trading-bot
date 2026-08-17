import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from learnerbot.solana_live_executor import SolanaLiveError, sign_versioned_transaction
from learnerbot import telegram_solana_live_patch as telegram_live


def _unsigned_single_signer(kp: Keypair) -> bytes:
    msg = MessageV0.try_compile(kp.pubkey(), [], [], Hash.default())
    return bytes(VersionedTransaction(msg, [NullSigner(kp.pubkey())]))


def test_sign_versioned_transaction_signs_exact_wallet_slot():
    kp = Keypair()
    signed_raw = sign_versioned_transaction(_unsigned_single_signer(kp), bytes(kp))
    tx = VersionedTransaction.from_bytes(signed_raw)
    assert tx.signatures[0].verify(kp.pubkey(), to_bytes_versioned(tx.message))


def test_live_canary_rejects_multi_signer_transaction():
    payer = Keypair()
    other = Keypair()
    ix = Instruction(Pubkey.new_unique(), b"x", [AccountMeta(other.pubkey(), True, False)])
    msg = MessageV0.try_compile(payer.pubkey(), [ix], [], Hash.default())
    tx = VersionedTransaction(msg, [NullSigner(payer.pubkey()), NullSigner(other.pubkey())])
    with pytest.raises(SolanaLiveError, match="requiring 2 signers"):
        sign_versioned_transaction(bytes(tx), bytes(payer))


def test_solana_live_keyboard_is_explicitly_user_armed(monkeypatch):
    monkeypatch.setattr(telegram_live, "live_enabled", lambda app, tid: False)
    kb = telegram_live.solana_keyboard(object(), "123")
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "sibot:solana:live:arm" in callbacks
    assert "sibot:solana:live:confirm" not in callbacks

    monkeypatch.setattr(telegram_live, "live_enabled", lambda app, tid: True)
    kb = telegram_live.solana_keyboard(object(), "123")
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "sibot:solana:live:off" in callbacks
