from __future__ import annotations

import csv
import os
import re
import secrets
import time
from pathlib import Path

from .user_registry import get_user


class SolanaWalletError(RuntimeError):
    pass


HEADERS = ["telegram_id", "wallet_id", "label", "address", "enabled", "active", "created_epoch", "notes"]
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
            raise SolanaWalletError("Invalid Solana address")
        n = n * 58 + _MAP[ch]
    raw = b"" if n == 0 else n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(value) - len(value.lstrip("1"))
    return (b"\x00" * pad) + raw


def validate_solana_address(address: str) -> str:
    value = str(address or "").strip()
    if not 32 <= len(value) <= 44:
        raise SolanaWalletError("Solana address must be a valid base58 public address")
    raw = _decode_base58(value)
    if len(raw) != 32 or not any(raw):
        raise SolanaWalletError("Solana address must decode to a 32-byte public key")
    return value


class SolanaWalletStore:
    """Public-address registry for Solana.

    No Solana private key or seed phrase is accepted or stored here.  This is the
    funding/identity address used by Solana SHADOW reporting until a separately
    audited signing implementation exists.
    """

    def __init__(self, csv_dir: Path):
        self.csv_dir = Path(csv_dir)
        self.path = self.csv_dir / "auto" / "solana_user_wallets.csv"

    def _max_wallets(self, telegram_id) -> int:
        u = get_user(self.csv_dir, telegram_id) or {}
        try:
            return max(1, min(50, int(float(u.get("max_solana_wallets") or u.get("max_wallets") or 5))))
        except Exception:
            return 5

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
            "enabled": "true",
            "active": "true" if active else "false",
            "created_epoch": int(time.time()),
            "notes": "public-address-only",
        }
        rows.append(row)
        _write(self.path, rows)
        return dict(row)

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
        remaining = [r for r in rows if str(r.get("telegram_id") or "").strip() == tid and str(r.get("enabled", "true")).lower() in {"1", "true", "yes", "on"}]
        if remaining and not any(str(r.get("active") or "").lower() == "true" for r in remaining):
            remaining[0]["active"] = "true"
        _write(self.path, rows)

    def has_wallet(self, telegram_id) -> bool:
        return bool(self.list_wallets(telegram_id))
