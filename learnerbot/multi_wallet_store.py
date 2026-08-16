from __future__ import annotations

import csv
import json
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account

from .user_registry import get_user


class MultiWalletError(RuntimeError):
    pass


REGISTRY_HEADERS = ["telegram_id","wallet_id","label","address","source","enabled","active","created_epoch","notes"]


def _safe_tid(telegram_id) -> str:
    tid = str(telegram_id).strip()
    if not re.fullmatch(r"-?\d{1,24}", tid):
        raise MultiWalletError("Invalid Telegram ID")
    return tid


def _rows(path: Path):
    if not path.exists(): return []
    with path.open("r",encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))


def _atomic_write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=REGISTRY_HEADERS);w.writeheader();w.writerows([{h:r.get(h,"") for h in REGISTRY_HEADERS} for r in rows]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


class MultiWalletStore:
    """Encrypted multi-user EVM wallet store.

    Private keys never go into CSV. CSV contains only public metadata and ownership.
    Every decrypt operation requires the Telegram owner id and wallet id.
    """
    def __init__(self, data_dir: Path, csv_dir: Path):
        self.data_dir=Path(data_dir);self.csv_dir=Path(csv_dir)
        self.key_path=self.data_dir/".multi_wallet_store.key"
        self.wallet_root=self.data_dir/"user_wallets"
        self.registry_path=self.csv_dir/"auto"/"user_wallets.csv"

    def _chmod600(self,p):
        try:os.chmod(p,0o600)
        except Exception:pass

    def _fernet(self,create=False):
        self.data_dir.mkdir(parents=True,exist_ok=True)
        if not self.key_path.exists():
            if not create:raise MultiWalletError("Multi-wallet encryption key is missing")
            self.key_path.write_bytes(Fernet.generate_key());self._chmod600(self.key_path)
        try:return Fernet(self.key_path.read_bytes().strip())
        except Exception as exc:raise MultiWalletError("Multi-wallet encryption key is invalid") from exc

    def _wallet_file(self,telegram_id,wallet_id):
        tid=_safe_tid(telegram_id);wid=str(wallet_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}",wid):raise MultiWalletError("Invalid wallet id")
        return self.wallet_root/tid/f"{wid}.enc.json"

    def list_wallets(self,telegram_id,enabled_only=True):
        tid=_safe_tid(telegram_id);out=[]
        for r in _rows(self.registry_path):
            if str(r.get("telegram_id","")).strip()!=tid:continue
            if enabled_only and str(r.get("enabled","true")).lower() not in {"1","true","yes","on"}:continue
            out.append(r)
        return out

    def _max_wallets(self,telegram_id):
        u=get_user(self.csv_dir,telegram_id) or {}
        try:return max(1,min(50,int(float(u.get("max_wallets") or 5))))
        except:return 5

    def save_private_key(self,telegram_id,private_key,label="Wallet",source="telegram-import"):
        tid=_safe_tid(telegram_id)
        if len(self.list_wallets(tid))>=self._max_wallets(tid):raise MultiWalletError("Maximum wallets reached for this Telegram account")
        key=str(private_key or "").strip()
        try:acct=Account.from_key(key)
        except Exception as exc:raise MultiWalletError("Invalid EVM private key") from exc
        wid="w"+secrets.token_hex(4);raw=bytes(acct.key);token=self._fernet(create=True).encrypt(raw).decode("ascii")
        p=self._wallet_file(tid,wid);p.parent.mkdir(parents=True,exist_ok=True)
        payload={"version":2,"telegram_id":tid,"wallet_id":wid,"address":acct.address,"source":source,"encrypted_private_key":token}
        tmp=p.with_suffix(".tmp");tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8");self._chmod600(tmp);os.replace(tmp,p);self._chmod600(p)
        rows=_rows(self.registry_path)
        if not any(str(r.get("telegram_id"))==tid and str(r.get("active","")).lower()=="true" for r in rows):active="true"
        else:active="false"
        rows.append({"telegram_id":tid,"wallet_id":wid,"label":str(label)[:50],"address":acct.address,"source":source,"enabled":"true","active":active,"created_epoch":int(time.time()),"notes":""})
        _atomic_write(self.registry_path,rows);return {"wallet_id":wid,"address":acct.address,"label":str(label)[:50],"active":active=="true"}

    def create(self,telegram_id,label="Wallet"):
        acct=Account.create(os.urandom(32));return self.save_private_key(telegram_id,acct.key.hex(),label=label,source="telegram-created")

    def get_meta(self,telegram_id,wallet_id=None):
        wallets=self.list_wallets(telegram_id)
        if not wallets:raise MultiWalletError("No wallet is configured for this Telegram account")
        if wallet_id:
            for r in wallets:
                if r.get("wallet_id")==wallet_id:return r
            raise MultiWalletError("Wallet id not found for this Telegram account")
        for r in wallets:
            if str(r.get("active","")).lower()=="true":return r
        return wallets[0]

    def private_key_hex(self,telegram_id,wallet_id=None):
        meta=self.get_meta(telegram_id,wallet_id);p=self._wallet_file(telegram_id,meta["wallet_id"])
        try:
            payload=json.loads(p.read_text(encoding="utf-8"));raw=self._fernet(False).decrypt(payload["encrypted_private_key"].encode("ascii"))
            acct=Account.from_key(raw)
            if acct.address.lower()!=str(meta.get("address","")).lower():raise MultiWalletError("Wallet metadata/address mismatch")
            return "0x"+raw.hex()
        except (KeyError,ValueError,InvalidToken,json.JSONDecodeError,FileNotFoundError) as exc:raise MultiWalletError("Cannot decrypt this wallet") from exc

    def address(self,telegram_id,wallet_id=None):return self.get_meta(telegram_id,wallet_id).get("address")

    def set_active(self,telegram_id,wallet_id):
        tid=_safe_tid(telegram_id);rows=_rows(self.registry_path);found=False
        for r in rows:
            if str(r.get("telegram_id","")).strip()!=tid:continue
            if r.get("wallet_id")==wallet_id and str(r.get("enabled","true")).lower()=="true":found=True
        if not found:raise MultiWalletError("Wallet id not found for this Telegram account")
        for r in rows:
            if str(r.get("telegram_id","")).strip()==tid:r["active"]="true" if r.get("wallet_id")==wallet_id else "false"
        _atomic_write(self.registry_path,rows);return self.get_meta(tid,wallet_id)

    def forget(self,telegram_id,wallet_id):
        tid=_safe_tid(telegram_id);rows=_rows(self.registry_path);target=None
        for r in rows:
            if str(r.get("telegram_id","")).strip()==tid and r.get("wallet_id")==wallet_id:
                target=r;r["enabled"]="false";r["active"]="false";break
        if target is None:raise MultiWalletError("Wallet id not found for this Telegram account")
        p=self._wallet_file(tid,wallet_id)
        if p.exists():p.unlink()
        # make another enabled wallet active if needed
        remaining=[r for r in rows if str(r.get("telegram_id","")).strip()==tid and str(r.get("enabled","true")).lower()=="true"]
        if remaining and not any(str(r.get("active","")).lower()=="true" for r in remaining):remaining[0]["active"]="true"
        _atomic_write(self.registry_path,rows)

    def has_wallet(self,telegram_id):
        return bool(self.list_wallets(telegram_id))
