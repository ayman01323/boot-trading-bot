"""Signing readiness gate for claude-trading-bot.

Reuses learnerbot.solana_wallet_store.SolanaWalletStore -- the existing,
reviewed encrypted keystore (Fernet-encrypted keypair files, key material
never in a CSV, never logged) -- rather than writing new key-handling code.
Key storage/encryption is security infrastructure, not strategy, and this
project already has a reviewed implementation of it.

This module answers exactly one question: is a signing key present for this
instance's wallet, yes or no. It does not decide to broadcast anything --
that decision still goes through the existing ARMED/LIVE_TRADING platform
gates plus risk_engine_guard.py, unchanged. Until GPT/operator provisions a
dedicated signing wallet on botgoogle, this reports SIGNER_READY=false and
every caller must treat that as "broadcast unavailable."

Security discipline: nothing in this module ever logs, prints, or returns
decrypted key bytes except get_signing_keypair_bytes(), which exists solely
for an execution engine to consume at the moment of signing. Callers must
never log its return value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from learnerbot.solana_wallet_store import SolanaWalletError, SolanaWalletStore

_OWNER_ID_PATTERN = re.compile(r"-?\d{1,24}")


class SignerNotReadyError(RuntimeError):
    """Raised by get_signing_keypair_bytes() when no signing key is available."""


@dataclass(frozen=True)
class SignerStatus:
    ready: bool
    reason: str
    address: str | None = None  # public address only -- safe to log/report


def _owner_id() -> str:
    owner_id = os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()
    if not owner_id or not _OWNER_ID_PATTERN.fullmatch(owner_id):
        raise SolanaWalletError(
            "CLAUDE_BOT_WALLET_OWNER_ID is not set to a valid identifier "
            "(required to know which wallet-store row governs signing)"
        )
    return owner_id


def get_signer_status(app) -> SignerStatus:
    """Check signing readiness. Never raises -- always returns a SignerStatus."""
    try:
        owner_id = _owner_id()
    except SolanaWalletError as exc:
        return SignerStatus(ready=False, reason=f"SIGNER_READY=false: {exc}")

    store = SolanaWalletStore(csv_dir=app.csv_dir, data_dir=app.data_dir)
    try:
        if not store.has_private_key(owner_id):
            return SignerStatus(
                ready=False,
                reason=(
                    "SIGNER_READY=false: no encrypted signing key provisioned yet "
                    f"for wallet owner id {owner_id} under this instance's isolated "
                    "DATA_DIR. GPT/operator must provision it separately -- this "
                    "bot never asks for a private key in chat or source."
                ),
            )
        meta = store.get_meta(owner_id)
        return SignerStatus(ready=True, reason="SIGNER_READY=true", address=meta.get("address"))
    except SolanaWalletError as exc:
        return SignerStatus(ready=False, reason=f"SIGNER_READY=false: {exc}")


def get_signing_keypair_bytes(app) -> bytes:
    """Return the raw decrypted keypair bytes for signing. Caller must never log this.

    Raises SignerNotReadyError if no signing key is provisioned. Intended to be
    called only at the moment of constructing/signing a transaction, by an
    execution engine that is itself already gated by ARMED/LIVE_TRADING and
    risk_engine_guard -- this function does not check those gates itself, it
    only answers "can we sign at all."
    """
    status = get_signer_status(app)
    if not status.ready:
        raise SignerNotReadyError(status.reason)
    owner_id = _owner_id()
    store = SolanaWalletStore(csv_dir=app.csv_dir, data_dir=app.data_dir)
    return store.keypair_bytes(owner_id)
