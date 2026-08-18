from __future__ import annotations

import threading
import time
from pathlib import Path

import requests

from .config import AppSettings
from . import telegram_ui as _ui
from .transaction_audit import AUDIT_INTERVAL_SECONDS, run_transaction_audit
from .user_registry import all_users

_PREV_START_MENU_THREAD = _ui.start_menu_thread
_STARTED = False
_LOCK = threading.Lock()


def _master_chat_ids(app) -> list[str]:
    out = []
    for row in all_users(app.csv_dir, enabled_only=False):
        if str(row.get("role") or "").upper() != "MASTER":
            continue
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        tid = str(row.get("telegram_id") or "").strip()
        if tid and tid not in out:
            out.append(tid)
    if not out:
        for tid in getattr(app, "telegram_chat_ids", []) or []:
            value = str(tid).strip()
            if value and value not in out:
                out.append(value)
            if out:
                break
    return out[:3]


def send_audit_document(app, chat_id: str, zip_path: str, summary: dict) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return
    path = Path(zip_path)
    if not path.exists():
        return
    caption = (
        "📦 2-hour all-ID transaction audit\n"
        f"Users: {summary.get('registered_users', 0)} • Wallets: {summary.get('enabled_wallets', 0)}\n"
        f"Solana tx: {summary.get('solana_transactions', 0)} • EVM rows: {summary.get('evm_event_rows', 0)}\n"
        f"Collection errors: {summary.get('collection_errors', 0)}\n"
        "Upload this ZIP to ChatGPT to review realised results and update the trading strategy."
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with path.open("rb") as fh:
        response = requests.post(
            url,
            data={
                "chat_id": str(chat_id),
                "caption": caption[:1000],
                "protect_content": "true",
                "disable_notification": "true",
            },
            files={"document": (path.name, fh, "application/zip")},
            timeout=90,
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendDocument failed: {payload}")


def _audit_loop(seed_app):
    # Start the first audit shortly after a service restart so deployment itself
    # can be used to trigger an immediate collection. Subsequent runs remain on
    # the normal two-hour cadence.
    time.sleep(10)
    while True:
        started = time.time()
        try:
            app = AppSettings.load()
        except Exception:
            app = seed_app
        try:
            summary = run_transaction_audit(app, hours=2.0)
            print(
                "[transaction-audit] users=%s wallets=%s solana=%s evm=%s errors=%s zip=%s"
                % (
                    summary.get("registered_users", 0),
                    summary.get("enabled_wallets", 0),
                    summary.get("solana_transactions", 0),
                    summary.get("evm_event_rows", 0),
                    summary.get("collection_errors", 0),
                    summary.get("latest_zip", ""),
                )
            )
            for tid in _master_chat_ids(app):
                try:
                    send_audit_document(app, tid, summary["latest_zip"], summary)
                except Exception as exc:
                    print("[transaction-audit-telegram]", tid, type(exc).__name__, str(exc)[:300])
        except Exception as exc:
            print("[transaction-audit] ERROR", type(exc).__name__, str(exc)[:500])

        elapsed = max(0.0, time.time() - started)
        sleep_for = max(60.0, float(AUDIT_INTERVAL_SECONDS) - elapsed)
        time.sleep(sleep_for)


def start_menu_thread_with_transaction_audit(app):
    global _STARTED
    result = _PREV_START_MENU_THREAD(app)
    with _LOCK:
        if not _STARTED:
            _STARTED = True
            threading.Thread(
                target=_audit_loop,
                args=(app,),
                daemon=True,
                name="all-user-transaction-audit",
            ).start()
            print("[transaction-audit] scheduled every 2 hours; MASTER ZIP delivery enabled")
    return result


def install():
    _ui.start_menu_thread = start_menu_thread_with_transaction_audit


install()
