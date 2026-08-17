from __future__ import annotations

import csv
import json
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .user_registry import get_user


class SolanaWalletError(RuntimeError):
    pass


HEADERS = ["telegram_id", "wallet_id", "label", "address", "signing", "enabled", "active", "created_epoch", "notes"]
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_MAP = {c: i for i, c in enumerate(_ALPHABET)}


def _safe_tid(telegram_id) -> str:
    tid = str(telegram_id).strip()
    if not re.fullmatch(r"-?\d{1,24}", tid):
        raise SolanaWalletError("Invalid Telegram ID")
    return tid


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows([{h: r.get(h, "") for h in HEADERS} for r in rows])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _decode_base58(value: str) -> bytes:
    n = 0
    for ch in value:
        if ch not in _MAP:
            raise SolanaWalletError("Invalid Solana base58 value")
        n = n * 58 + _MAP[ch]
    raw = b"" if n == 0 else n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(value) - len(value.lstrip("1"))
    return (b"\x00" * pad) + raw


def _encode_base58(raw: bytes) -> str:
    raw = bytes(raw)
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    n = int.from_bytes(raw, "big")
    chars = []
    while n:
        n, rem = divmod(n, 58)
        chars.append(_ALPHABET[rem])
    body = "".join(reversed(chars))
    return ("1" * pad) + body


def validate_solana_address(address: str) -> str:
    value = str(address or "").strip()
    if not 32 <= len(value) <= 44:
        raise SolanaWalletError("Solana address must be a valid base58 public address")
    raw = _decode_base58(value)
    if len(raw) != 32 or not any(raw):
        raise SolanaWalletError("Solana address must decode to a 32-byte public key")
    return value


def parse_solana_private_key(value: str) -> tuple[bytes, str]:
    """Return verified 64-byte Solana keypair bytes and its public address.

    Accepted secret formats are the standard base58-encoded 64-byte keypair and
    the Solana CLI JSON/U8Array form containing exactly 64 integers. Seed
    phrases, file paths and ambiguous 32-byte seeds are intentionally rejected.
    """
    text = str(value or "").strip()
    if not text:
        raise SolanaWalletError("Solana private key is empty")
    try:
        if text.startswith("["):
            data = json.loads(text)
            if not isinstance(data, list) or len(data) != 64:
                raise SolanaWalletError("Expected a JSON array containing exactly 64 bytes")
            if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 or x > 255 for x in data):
                raise SolanaWalletError("Solana JSON keypair must contain only byte values 0-255")
            raw = bytes(data)
        else:
            raw = _decode_base58(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SolanaWalletError("Invalid Solana private key format") from exc
    if len(raw) != 64:
        raise SolanaWalletError("Expected a 64-byte Solana keypair in base58 or JSON-array form; seed phrases are not accepted")
    try:
        public = Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    except Exception as exc:
        raise SolanaWalletError("Invalid Solana Ed25519 private key") from exc
    if raw[32:] != public:
        raise SolanaWalletError("Solana keypair public-key bytes do not match the private key")
    return raw, _encode_base58(public)


class SolanaWalletStore:
    """Per-Telegram Solana wallet registry with optional encrypted signing keys.

    CSV contains public metadata only. When data_dir is provided, imported
    64-byte Solana keypairs are encrypted into owner-scoped files. The store
    deliberately does not accept seed phrases.
    """

    def __init__(self, csv_dir: Path, data_dir: Path | None = None):
        self.csv_dir = Path(csv_dir)
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.path = self.csv_dir / "auto" / "solana_user_wallets.csv"
        self.key_path = (self.data_dir / ".solana_wallet_store.key") if self.data_dir is not None else None
        self.wallet_root = (self.data_dir / "user_solana_wallets") if self.data_dir is not None else None

    def _max_wallets(self, telegram_id) -> int:
        u = get_user(self.csv_dir, telegram_id) or {}
        try:
            return max(1, min(50, int(float(u.get("max_solana_wallets") or u.get("max_wallets") or 5))))
        except Exception:
            return 5

    def _chmod600(self, path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def _fernet(self, create=False):
        if self.data_dir is None or self.key_path is None:
            raise SolanaWalletError("Solana encrypted signing store is not configured")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            if not create:
                raise SolanaWalletError("Solana wallet encryption key is missing")
            self.key_path.write_bytes(Fernet.generate_key())
            self._chmod600(self.key_path)
        try:
            return Fernet(self.key_path.read_bytes().strip())
        except Exception as exc:
            raise SolanaWalletError("Solana wallet encryption key is invalid") from exc

    def _wallet_file(self, telegram_id, wallet_id) -> Path:
        if self.wallet_root is None:
            raise SolanaWalletError("Solana encrypted signing store is not configured")
        tid = _safe_tid(telegram_id)
        wid = str(wallet_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", wid):
            raise SolanaWalletError("Invalid Solana wallet id")
        return self.wallet_root / tid / f"{wid}.enc.json"

    def list_wallets(self, telegram_id, enabled_only=True) -> list[dict]:
        tid = _safe_tid(telegram_id)
        out = []
        for row in _rows(self.path):
            if str(row.get("telegram_id") or "").strip() != tid:
                continue
            if enabled_only and str(row.get("enabled", "true")).lower() not in {"1", "true", "yes", "on"}:
                continue
            out.append(row)
        return out

    def add(self, telegram_id, address: str, label="Solana") -> dict:
        tid = _safe_tid(telegram_id)
        address = validate_solana_address(address)
        current = self.list_wallets(tid)
        if len(current) >= self._max_wallets(tid):
            raise SolanaWalletError("Maximum Solana wallets reached for this Telegram account")
        if any(str(r.get("address") or "") == address for r in current):
            raise SolanaWalletError("This Solana address is already added")
        rows = _rows(self.path)
        active = not any(
            str(r.get("telegram_id") or "").strip() == tid
            and str(r.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}
            and str(r.get("active", "")).lower() == "true"
            for r in rows
        )
        wid = "s" + secrets.token_hex(4)
        row = {
            "telegram_id": tid,
            "wallet_id": wid,
            "label": str(label or "Solana")[:50],
            "address": address,
            "signing": "false",
            "enabled": "true",
            "active": "true" if active else "false",
            "created_epoch": int(time.time()),
            "notes": "public-address-only",
        }
        rows.append(row)
        _write(self.path, rows)
        return dict(row)

    def save_private_key(self, telegram_id, private_key: str, label="Imported Solana", source="telegram-import") -> dict:
        tid = _safe_tid(telegram_id)
        raw, address = parse_solana_private_key(private_key)
        rows = _rows(self.path)
        target = None
        for row in rows:
            if (
                str(row.get("telegram_id") or "").strip() == tid
                and str(row.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}
                and str(row.get("address") or "") == address
            ):
                target = row
                break
        if target is None:
            if len(self.list_wallets(tid)) >= self._max_wallets(tid):
                raise SolanaWalletError("Maximum Solana wallets reached for this Telegram account")
            active = not any(
                str(r.get("telegram_id") or "").strip() == tid
                and str(r.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}
                and str(r.get("active", "")).lower() == "true"
                for r in rows
            )
            target = {
                "telegram_id": tid,
                "wallet_id": "s" + secrets.token_hex(4),
                "label": str(label or "Imported Solana")[:50],
                "address": address,
                "signing": "true",
                "enabled": "true",
                "active": "true" if active else "false",
                "created_epoch": int(time.time()),
                "notes": "encrypted-signing-key",
            }
            rows.append(target)
        else:
            target["signing"] = "true"
            target["notes"] = "encrypted-signing-key"
            if not str(target.get("label") or "").strip():
                target["label"] = str(label or "Imported Solana")[:50]
        p = self._wallet_file(tid, target["wallet_id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet(create=True).encrypt(raw).decode("ascii")
        payload = {
            "version": 1,
            "telegram_id": tid,
            "wallet_id": target["wallet_id"],
            "address": address,
            "source": str(source or "telegram-import")[:40],
            "encrypted_keypair": token,
        }
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._chmod600(tmp)
        os.replace(tmp, p)
        self._chmod600(p)
        _write(self.path, rows)
        return dict(target)

    def get_meta(self, telegram_id, wallet_id=None) -> dict:
        wallets = self.list_wallets(telegram_id)
        if not wallets:
            raise SolanaWalletError("No Solana wallet is configured")
        if wallet_id:
            for row in wallets:
                if row.get("wallet_id") == wallet_id:
                    return row
            raise SolanaWalletError("Solana wallet id not found")
        for row in wallets:
            if str(row.get("active") or "").lower() == "true":
                return row
        return wallets[0]

    def has_private_key(self, telegram_id, wallet_id=None) -> bool:
        try:
            meta = self.get_meta(telegram_id, wallet_id)
            if str(meta.get("signing") or "").lower() != "true":
                return False
            return self._wallet_file(telegram_id, meta["wallet_id"]).exists()
        except Exception:
            return False

    def keypair_bytes(self, telegram_id, wallet_id=None) -> bytes:
        meta = self.get_meta(telegram_id, wallet_id)
        p = self._wallet_file(telegram_id, meta["wallet_id"])
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            raw = self._fernet(False).decrypt(payload["encrypted_keypair"].encode("ascii"))
            checked, address = parse_solana_private_key(_encode_base58(raw))
            if address != str(meta.get("address") or "") or checked != raw:
                raise SolanaWalletError("Solana wallet metadata/address mismatch")
            return raw
        except SolanaWalletError:
            raise
        except (KeyError, ValueError, InvalidToken, json.JSONDecodeError, FileNotFoundError) as exc:
            raise SolanaWalletError("Cannot decrypt this Solana wallet") from exc

    def set_active(self, telegram_id, wallet_id: str) -> dict:
        tid = _safe_tid(telegram_id)
        rows = _rows(self.path)
        found = False
        for row in rows:
            if str(row.get("telegram_id") or "").strip() == tid and row.get("wallet_id") == wallet_id and str(row.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}:
                found = True
        if not found:
            raise SolanaWalletError("Solana wallet id not found")
        for row in rows:
            if str(row.get("telegram_id") or "").strip() == tid:
                row["active"] = "true" if row.get("wallet_id") == wallet_id else "false"
        _write(self.path, rows)
        return self.get_meta(tid, wallet_id)

    def forget(self, telegram_id, wallet_id: str) -> None:
        tid = _safe_tid(telegram_id)
        rows = _rows(self.path)
        target = None
        for row in rows:
            if str(row.get("telegram_id") or "").strip() == tid and row.get("wallet_id") == wallet_id:
                target = row
                row["enabled"] = "false"
                row["active"] = "false"
                break
        if target is None:
            raise SolanaWalletError("Solana wallet id not found")
        if self.data_dir is not None:
            try:
                p = self._wallet_file(tid, wallet_id)
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        remaining = [r for r in rows if str(r.get("telegram_id") or "").strip() == tid and str(r.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}]
        if remaining and not any(str(r.get("active") or "").lower() == "true" for r in remaining):
            remaining[0]["active"] = "true"
        _write(self.path, rows)

    def has_wallet(self, telegram_id) -> bool:
        return bool(self.list_wallets(telegram_id))
