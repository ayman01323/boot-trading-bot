from __future__ import annotations

"""Securely repair the isolated SiLearn MASTER mapping from a live private command.

The exact Telegram chat id presented by /learnerbot is compared against trusted
MASTER/recipient records already present on the same server.  No id or token is
logged.  A repair is allowed only with corroboration from at least two distinct
external source files, including one structured users.csv MASTER registry.
"""

import csv
import glob
import os
import re
import time
from pathlib import Path

from . import telegram_ui as _ui
from . import telegram_google_learner_launcher_patch as _learner

_PREV_HANDLE_UPDATE = _ui.handle_update
_ROOT = Path("/home/ayman01323/BOOT/testingbots/learn")
_USERS = _ROOT / "CSVbot" / "users.csv"
_ENV = _ROOT / ".env"
_PRIVATE = _ROOT / "private"
_CAPTURE = _PRIVATE / ".telegram_master_candidate"
_INSTALLED_ATTR = "_learner_master_autopair_installed"


def _valid_id(value) -> str:
    s = str(value or "").strip()
    return s if s and s.lstrip("-").isdigit() else ""


def _trusted_sources_for(tid: str) -> tuple[set[str], set[str]]:
    sources: set[str] = set()
    kinds: set[str] = set()

    csv_paths: set[str] = set()
    for pat in (
        "/root/multichain-learning-bot-v2.2-fast-direct-market/CSVbot/users.csv",
        "/home/ayman01323/BOOT/CSVbot/users.csv",
        "/home/ayman01323/BOOT/testingbots/*/CSVbot/users.csv",
    ):
        csv_paths.update(glob.glob(pat))

    try:
        learner_users = _USERS.resolve()
    except Exception:
        learner_users = _USERS

    for raw in sorted(csv_paths):
        p = Path(raw)
        try:
            if p.resolve() == learner_users or not p.is_file():
                continue
            rows = list(csv.DictReader(p.open(encoding="utf-8-sig", newline="")))
        except Exception:
            continue
        for row in rows:
            if str(row.get("status") or "").upper() != "ACTIVE":
                continue
            if str(row.get("role") or "").upper() != "MASTER":
                continue
            if _valid_id(row.get("telegram_id")) == tid:
                sources.add("users:" + str(p))
                kinds.add("users")
                break

    env_paths: set[str] = set()
    for pat in (
        "/root/multichain-learning-bot-v2.2-fast-direct-market/.env",
        "/home/ayman01323/BOOT/.env",
        "/home/ayman01323/BOOT/testingbots/*/.env",
        "/var/tmp/*telegram*runtime*.env",
        "/var/tmp/*council*runtime*.env",
    ):
        env_paths.update(glob.glob(pat))

    try:
        learner_env = _ENV.resolve()
    except Exception:
        learner_env = _ENV
    chat_key = re.compile(r"^(?:[A-Z0-9_]*TELEGRAM_[A-Z0-9_]*CHAT_IDS?|TELEGRAM_CHAT_IDS?)$")

    for raw in sorted(env_paths):
        p = Path(raw)
        try:
            if p.resolve() == learner_env or not p.is_file():
                continue
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        matched = False
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            if not chat_key.match(key.strip().upper()):
                continue
            value = value.strip().strip('"').strip("'")
            for item in re.split(r"[,;\s]+", value):
                if _valid_id(item) == tid:
                    matched = True
                    break
            if matched:
                break
        if matched:
            sources.add("env:" + str(p))
            kinds.add("env")

    return sources, kinds


def _capture_candidate(tid: str) -> None:
    try:
        _PRIVATE.mkdir(parents=True, exist_ok=True)
        tmp = _CAPTURE.with_suffix(".tmp")
        tmp.write_text(f"{tid}\n{int(time.time())}\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, _CAPTURE)
        os.chmod(_CAPTURE, 0o600)
    except Exception:
        pass


def _atomic_repair(tid: str) -> bool:
    sources, kinds = _trusted_sources_for(tid)
    if len(sources) < 2 or "users" not in kinds:
        return False

    try:
        rows = list(csv.DictReader(_USERS.open(encoding="utf-8-sig", newline="")))
    except Exception:
        return False

    fields = [
        "telegram_id", "role", "status", "fee_plan_id", "label", "allowed_chains",
        "max_wallets", "can_transfer", "can_manual_trade", "can_auto_trade",
        "created_epoch", "activated_epoch", "notes",
    ]
    now = str(int(time.time()))
    found = False
    for row in rows:
        rid = _valid_id(row.get("telegram_id"))
        if str(row.get("role") or "").upper() == "MASTER" and rid != tid:
            row["role"] = "USER"
            row["status"] = "SUSPENDED"
            row["notes"] = "Superseded by secure live SiLearn MASTER pairing " + now
        if rid == tid:
            row["role"] = "MASTER"
            row["status"] = "ACTIVE"
            row["fee_plan_id"] = "MASTER"
            row["label"] = row.get("label") or "Master"
            row["allowed_chains"] = "*"
            row["max_wallets"] = row.get("max_wallets") or "20"
            row["can_transfer"] = "true"
            row["can_manual_trade"] = "true"
            row["can_auto_trade"] = "true"
            row["activated_epoch"] = row.get("activated_epoch") or now
            row["notes"] = "Verified by live command plus cross-runtime MASTER consensus " + now
            found = True

    if not found:
        rows.append({
            "telegram_id": tid,
            "role": "MASTER",
            "status": "ACTIVE",
            "fee_plan_id": "MASTER",
            "label": "Master",
            "allowed_chains": "*",
            "max_wallets": "20",
            "can_transfer": "true",
            "can_manual_trade": "true",
            "can_auto_trade": "true",
            "created_epoch": now,
            "activated_epoch": now,
            "notes": "Verified by live command plus cross-runtime MASTER consensus " + now,
        })

    try:
        _PRIVATE.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time.time()))
        if _USERS.exists():
            (_PRIVATE / ("users.before-live-master-pair-" + stamp + ".csv")).write_bytes(_USERS.read_bytes())
        if _ENV.exists():
            (_PRIVATE / (".env.before-live-master-pair-" + stamp)).write_bytes(_ENV.read_bytes())

        tmp_users = _USERS.with_suffix(".csv.tmp-master-pair")
        with tmp_users.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows([{key: row.get(key, "") for key in fields} for row in rows])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_users, _USERS)

        lines = _ENV.read_text(encoding="utf-8", errors="ignore").splitlines() if _ENV.exists() else []
        out: list[str] = []
        seen: set[str] = set()
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                out.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in {"TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_IDS"}:
                if key not in seen:
                    out.append(key + "=" + tid)
                    seen.add(key)
            else:
                out.append(line)
        for key in ("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_IDS"):
            if key not in seen:
                out.append(key + "=" + tid)
        tmp_env = _ENV.with_suffix(".env.tmp-master-pair")
        tmp_env.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        os.chmod(tmp_env, 0o600)
        os.replace(tmp_env, _ENV)
        os.chmod(_ENV, 0o600)
        try:
            _CAPTURE.unlink(missing_ok=True)
        except Exception:
            pass
        return True
    except Exception:
        return False


def handle_update(app, update):
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    tid = _valid_id(chat.get("id"))
    text = str(message.get("text") or "").strip()
    if tid and str(chat.get("type") or "") == "private" and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/learnerbot", "/learnergoogle"}:
            try:
                already_master = bool(_ui._master(app, tid))
            except Exception:
                already_master = False
            if not already_master:
                _capture_candidate(tid)
                if _atomic_repair(tid):
                    _ui._send(app, tid, _learner.learner_page(tid), _learner.learner_keyboard(tid))
                    return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, _INSTALLED_ATTR, False):
        return
    _ui.handle_update = handle_update
    setattr(_ui, _INSTALLED_ATTR, True)


install()
