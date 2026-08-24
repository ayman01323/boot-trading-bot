from __future__ import annotations

import csv
import hashlib
import os
import secrets
import time
from pathlib import Path


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _rows(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _atomic_write(path: Path, rows: list[dict], headers: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows([{h: r.get(h, "") for h in headers} for r in rows])
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


USER_HEADERS = [
    "telegram_id", "role", "status", "fee_plan_id", "label", "allowed_chains",
    "max_wallets", "can_transfer", "can_manual_trade", "can_auto_trade",
    "created_epoch", "activated_epoch", "notes",
]

ACTIVATION_HEADERS = [
    "code_hash", "plan_id", "enabled", "max_uses", "uses", "expires_epoch", "notes"
]

USER_SETTING_HEADERS = ["telegram_id", "chain_id", "setting", "value", "description"]


def users_path(csv_dir: Path) -> Path:
    return Path(csv_dir) / "users.csv"


def activation_codes_path(csv_dir: Path) -> Path:
    return Path(csv_dir) / "activation_codes.csv"


def user_settings_path(csv_dir: Path) -> Path:
    return Path(csv_dir) / "user_trading_settings.csv"


def ensure_master_seed(app):
    """Seed the first legacy TELEGRAM_CHAT_IDS entry as MASTER only when users.csv has no rows."""
    path = users_path(app.csv_dir)
    rows = _rows(path)
    if rows:
        return
    legacy = [str(x).strip() for x in getattr(app, "telegram_chat_ids", []) if str(x).strip()]
    if not legacy:
        _atomic_write(path, [], USER_HEADERS)
        return
    now = int(time.time()); rows=[]
    for i, tid in enumerate(legacy):
        master = i == 0
        rows.append({
            "telegram_id": tid, "role": "MASTER" if master else "USER", "status": "ACTIVE", "fee_plan_id": "MASTER" if master else "STANDARD",
            "label": "Master" if master else f"Legacy User {tid}", "allowed_chains": "*", "max_wallets": "20" if master else "5", "can_transfer": "true",
            "can_manual_trade": "true", "can_auto_trade": "true", "created_epoch": now,
            "activated_epoch": now, "notes": "Migrated from TELEGRAM_CHAT_IDS; first entry is MASTER",
        })
    _atomic_write(path, rows, USER_HEADERS)


def get_user(csv_dir: Path, telegram_id) -> dict | None:
    tid = str(telegram_id)
    for row in _rows(users_path(csv_dir)):
        if str(row.get("telegram_id", "")).strip() == tid:
            return row
    return None


def all_users(csv_dir: Path, *, enabled_only=False) -> list[dict]:
    out = []
    for row in _rows(users_path(csv_dir)):
        if enabled_only and (row.get("status") or "").upper() not in {"ACTIVE", "PENDING"}:
            continue
        out.append(row)
    return out


def is_registered(csv_dir: Path, telegram_id) -> bool:
    return get_user(csv_dir, telegram_id) is not None


def is_active(csv_dir: Path, telegram_id) -> bool:
    u = get_user(csv_dir, telegram_id)
    return bool(u and (u.get("status") or "").upper() == "ACTIVE")


def is_master(csv_dir: Path, telegram_id) -> bool:
    u = get_user(csv_dir, telegram_id)
    return bool(u and (u.get("role") or "USER").upper() == "MASTER" and (u.get("status") or "").upper() == "ACTIVE")


def _allowed_chain(user: dict, chain_slug: str) -> bool:
    raw = (user.get("allowed_chains") or "*").strip().lower()
    if raw in {"", "*", "all"}:
        return True
    allowed = {x.strip() for x in raw.replace("|", ",").split(",") if x.strip()}
    return chain_slug.lower() in allowed


def require_user(csv_dir: Path, telegram_id, *, active=False, chain_slug: str | None = None) -> dict:
    u = get_user(csv_dir, telegram_id)
    if not u:
        raise ValueError("Telegram ID is not registered. Use /join or /activate CODE.")
    status = (u.get("status") or "").upper()
    if status == "SUSPENDED":
        raise ValueError("This Telegram user is suspended.")
    if active and status != "ACTIVE":
        raise ValueError("Account is not active. Complete the activation fee or use an activation code.")
    if chain_slug and not _allowed_chain(u, chain_slug):
        raise ValueError(f"Your account is not permitted to use chain {chain_slug}.")
    return u


def join_user(csv_dir: Path, telegram_id, default_plan_id="STANDARD") -> dict:
    tid = str(telegram_id)
    existing = get_user(csv_dir, tid)
    if existing:
        return existing
    rows = _rows(users_path(csv_dir)); now = int(time.time())
    row = {
        "telegram_id": tid, "role": "USER", "status": "PENDING", "fee_plan_id": default_plan_id,
        "label": f"User {tid}", "allowed_chains": "*", "max_wallets": "5", "can_transfer": "true",
        "can_manual_trade": "true", "can_auto_trade": "true", "created_epoch": now,
        "activated_epoch": "", "notes": "Self-registered via Telegram /join",
    }
    rows.append(row); _atomic_write(users_path(csv_dir), rows, USER_HEADERS)
    return row


def update_user(csv_dir: Path, telegram_id, **updates) -> dict:
    tid = str(telegram_id); path = users_path(csv_dir); rows = _rows(path); found = None
    for row in rows:
        if str(row.get("telegram_id", "")).strip() == tid:
            for k, v in updates.items():
                if k in USER_HEADERS:
                    row[k] = str(v)
            found = row
            break
    if found is None:
        raise ValueError("User not found")
    _atomic_write(path, rows, USER_HEADERS)
    return found


def activate_user(csv_dir: Path, telegram_id, plan_id: str | None = None, note=""):
    updates = {"status": "ACTIVE", "activated_epoch": int(time.time())}
    if plan_id:
        updates["fee_plan_id"] = plan_id
    if note:
        updates["notes"] = note
    return update_user(csv_dir, telegram_id, **updates)


def _code_hash(code: str) -> str:
    return hashlib.sha256(str(code).strip().encode("utf-8")).hexdigest()


def redeem_activation_code(csv_dir: Path, telegram_id, code: str) -> dict:
    code = str(code or "").strip()
    if not code:
        raise ValueError("Activation code is empty")
    path = activation_codes_path(csv_dir); rows = _rows(path); now = int(time.time()); target = None
    h = _code_hash(code)
    for row in rows:
        if (row.get("code_hash") or "").strip().lower() != h:
            continue
        if not _bool(row.get("enabled"), True):
            raise ValueError("Activation code is disabled")
        expires = int(float(row.get("expires_epoch") or 0))
        if expires and now > expires:
            raise ValueError("Activation code has expired")
        max_uses = int(float(row.get("max_uses") or 1)); uses = int(float(row.get("uses") or 0))
        if max_uses > 0 and uses >= max_uses:
            raise ValueError("Activation code has reached its usage limit")
        row["uses"] = str(uses + 1); target = row; break
    if target is None:
        raise ValueError("Activation code is invalid")
    _atomic_write(path, rows, ACTIVATION_HEADERS)
    plan_id = target.get("plan_id") or "STANDARD"
    plan_ok = any((r.get("plan_id") or "").strip() == plan_id and _bool(r.get("enabled"), True) for r in _rows(Path(csv_dir)/"fee_plans.csv"))
    if not plan_ok:
        raise ValueError("Activation code points to a missing or disabled fee plan")
    if not get_user(csv_dir, telegram_id):
        join_user(csv_dir, telegram_id, plan_id)
    return activate_user(csv_dir, telegram_id, plan_id, "Activated by code")


def create_activation_code(csv_dir: Path, plan_id="STANDARD", max_uses=1, expires_epoch=0, notes="") -> str:
    code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper()
    path = activation_codes_path(csv_dir); rows = _rows(path)
    rows.append({"code_hash": _code_hash(code), "plan_id": plan_id, "enabled": "true", "max_uses": max_uses,
                 "uses": 0, "expires_epoch": expires_epoch or "", "notes": notes})
    _atomic_write(path, rows, ACTIVATION_HEADERS)
    return code


def user_setting(csv_dir: Path, telegram_id, chain_id: int, setting: str, default=None):
    tid = str(telegram_id); value = default; target_scope = str(chain_id).strip()
    rows = _rows(user_settings_path(csv_dir))

    # Legacy global aliases (blank/0) are fallback only.
    for row in rows:
        if str(row.get("telegram_id", "")).strip() != tid:
            continue
        scope = str(row.get("chain_id", "*")).strip()
        if scope in {"", "0"} and (row.get("setting") or "").strip() == setting:
            value = row.get("value", default)

    # '*' is the canonical global scope and always wins over legacy aliases.
    for row in rows:
        if str(row.get("telegram_id", "")).strip() != tid:
            continue
        if str(row.get("chain_id", "*")).strip() == "*" and (row.get("setting") or "").strip() == setting:
            value = row.get("value", default)

    # Only a real chain id can override the canonical global value. 0 is a
    # historical global alias, not an executable chain-specific scope.
    if target_scope not in {"", "*", "0"}:
        for row in rows:
            if str(row.get("telegram_id", "")).strip() != tid:
                continue
            if str(row.get("chain_id", "")).strip() == target_scope and (row.get("setting") or "").strip() == setting:
                value = row.get("value", default)
    return value


def set_user_setting(csv_dir: Path, telegram_id, setting: str, value, *, chain_id="*", description=""):
    path = user_settings_path(csv_dir); rows = _rows(path); tid = str(telegram_id); scope = str(chain_id); found = False
    for row in rows:
        if str(row.get("telegram_id", "")).strip() == tid and str(row.get("chain_id", "*")).strip() == scope and (row.get("setting") or "").strip() == setting:
            row["value"] = str(value); 
            if description: row["description"] = description
            found = True; break
    if not found:
        rows.append({"telegram_id": tid, "chain_id": scope, "setting": setting, "value": value, "description": description})
    _atomic_write(path, rows, USER_SETTING_HEADERS)


def user_bool(csv_dir: Path, telegram_id, chain_id: int, setting: str, default=False) -> bool:
    return _bool(user_setting(csv_dir, telegram_id, chain_id, setting, "true" if default else "false"), default)
