from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account


class WalletStoreError(RuntimeError):
    pass


class WalletStore:
    """Small server-local encrypted EVM wallet store.

    The encryption key and encrypted wallet record both live on the server with
    mode 0600. This protects against accidental disclosure through CSV/log/file
    browsing; it is not a substitute for a hardware wallet or secrets manager
    if the server itself is compromised.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.key_path = self.data_dir / ".live_wallet_store.key"
        self.wallet_path = self.data_dir / "live_wallet.enc.json"

    def _chmod600(self, path: Path):
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def _fernet(self, create=False) -> Fernet:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            if not create:
                raise WalletStoreError("No Telegram-managed live wallet is configured")
            self.key_path.write_bytes(Fernet.generate_key())
            self._chmod600(self.key_path)
        key = self.key_path.read_bytes().strip()
        try:
            return Fernet(key)
        except Exception as exc:
            raise WalletStoreError("Wallet-store encryption key is invalid") from exc

    def exists(self) -> bool:
        return self.wallet_path.exists()

    def save_private_key(self, private_key: str, *, source: str) -> str:
        key = str(private_key or "").strip()
        try:
            acct = Account.from_key(key)
        except Exception as exc:
            raise WalletStoreError("Invalid EVM private key") from exc
        raw = bytes(acct.key)
        token = self._fernet(create=True).encrypt(raw).decode("ascii")
        payload = {
            "version": 1,
            "address": acct.address,
            "source": source,
            "encrypted_private_key": token,
        }
        tmp = self.wallet_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._chmod600(tmp)
        os.replace(tmp, self.wallet_path)
        self._chmod600(self.wallet_path)
        return acct.address

    def create(self) -> str:
        acct = Account.create(os.urandom(32))
        return self.save_private_key(acct.key.hex(), source="telegram-created")

    def private_key_hex(self) -> str:
        if self.wallet_path.exists():
            try:
                payload = json.loads(self.wallet_path.read_text(encoding="utf-8"))
                enc = payload["encrypted_private_key"].encode("ascii")
                raw = self._fernet(create=False).decrypt(enc)
                return "0x" + raw.hex()
            except (KeyError, ValueError, InvalidToken, json.JSONDecodeError) as exc:
                raise WalletStoreError("Cannot decrypt Telegram-managed live wallet") from exc
        # Backwards-compatible v1.8 environment wallet.
        env_key = os.getenv("LIVE_WALLET_PRIVATE_KEY", "").strip()
        if env_key:
            try:
                Account.from_key(env_key)
            except Exception as exc:
                raise WalletStoreError("LIVE_WALLET_PRIVATE_KEY is invalid") from exc
            return env_key
        raise WalletStoreError("No live wallet is configured")

    def address(self) -> str | None:
        try:
            return Account.from_key(self.private_key_hex()).address
        except WalletStoreError:
            return None

    def forget(self):
        if self.wallet_path.exists():
            self.wallet_path.unlink()
        # Keep the encryption key so a newly-created wallet can use the same store.


def live_wallet_key(app) -> str:
    return WalletStore(app.data_dir).private_key_hex()


def live_wallet_address(app) -> str | None:
    return WalletStore(app.data_dir).address()
