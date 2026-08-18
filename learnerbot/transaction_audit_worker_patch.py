from __future__ import annotations

import threading
import time
from pathlib import Path

import requests

from .config import AppSettings
from . import telegram_ui as _ui
from .transaction_audit import run_transaction_audit
from .hourly_gpt_strategy_review import run_hourly_gpt_review
from .user_registry import all_users

HOURLY_INTERVAL_SECONDS = 60 * 60
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
        "📦 1-hour all-ID transaction audit\n"
        f"Users: {summary.get('registered_users', 0)} • Wallets: {summary.get('enabled_wallets', 0)}\n"
        f"Solana tx: {summary.get('solana_transactions', 0)} • EVM rows: {summary.get('evm_event_rows', 0)}\n"
        f"Collection errors: {summary.get('collection_errors', 0)}\n"
        "GPT analysis runs automatically on the server. Live strategy promotion still requires explicit approval."
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


def send_gpt_review_message(app, chat_id: str, result: dict) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        return
    if not result.get("ok"):
        text = (
            "⚠️ Hourly GPT audit review failed\n"
            f"{str(result.get('error') or 'unknown error')[:900]}\n"
            "Transaction audit was still saved. No live trading settings were changed."
        )
    else:
        review = result.get("review") or {}
        findings = review.get("findings") or []
        lines = [
            "🧠 Hourly GPT trading-bot review",
            f"Status: {review.get('status', 'UNKNOWN')}",
            f"Action: {review.get('recommended_action', 'KEEP_CURRENT_LIVE_SETTINGS')}",
            str(review.get("executive_summary") or "")[:1200],
            "",
            "Top findings:",
        ]
        for finding in findings[:5]:
            lines.append(
                "• [%s/%s] %s" % (
                    finding.get("severity", ""),
                    finding.get("category", ""),
                    str(finding.get("interpretation") or finding.get("evidence") or "")[:450],
                )
            )
        lines += [
            "",
            "Candidate mode: SHADOW_ONLY",
            "LIVE promotion: explicit human approval required.",
        ]
        text = "\n".join(lines)
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": str(chat_id),
            "text": text[:3900],
            "protect_content": True,
            "disable_notification": True,
            "link_preview_options": {"is_disabled": True},
        },
        timeout=30,
    )
    response.raise_for_status()


def _audit_loop(seed_app):
    # Start shortly after restart; thereafter collect/analyse once per hour.
    time.sleep(10)
    while True:
        started = time.time()
        try:
            app = AppSettings.load()
        except Exception:
            app = seed_app

        summary = None
        gpt_result = None
        try:
            summary = run_transaction_audit(app, hours=1.0)
            print(
                "[transaction-audit] hourly users=%s wallets=%s solana=%s evm=%s errors=%s zip=%s"
                % (
                    summary.get("registered_users", 0),
                    summary.get("enabled_wallets", 0),
                    summary.get("solana_transactions", 0),
                    summary.get("evm_event_rows", 0),
                    summary.get("collection_errors", 0),
                    summary.get("latest_zip", ""),
                )
            )
        except Exception as exc:
            print("[transaction-audit] ERROR", type(exc).__name__, str(exc)[:500])

        if summary is not None:
            try:
                gpt_result = run_hourly_gpt_review(app, summary["latest_zip"])
                print(
                    "[hourly-gpt-review] ok=%s mode=%s report=%s"
                    % (
                        gpt_result.get("ok"),
                        gpt_result.get("mode"),
                        gpt_result.get("latest_report", ""),
                    )
                )
            except Exception as exc:
                gpt_result = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "mode": "SHADOW_ONLY",
                    "live_auto_deploy": False,
                }
                print("[hourly-gpt-review] ERROR", type(exc).__name__, str(exc)[:500])

            for tid in _master_chat_ids(app):
                try:
                    send_audit_document(app, tid, summary["latest_zip"], summary)
                except Exception as exc:
                    print("[transaction-audit-telegram]", tid, type(exc).__name__, str(exc)[:300])
                if gpt_result is not None:
                    try:
                        send_gpt_review_message(app, tid, gpt_result)
                    except Exception as exc:
                        print("[hourly-gpt-review-telegram]", tid, type(exc).__name__, str(exc)[:300])

        elapsed = max(0.0, time.time() - started)
        sleep_for = max(60.0, float(HOURLY_INTERVAL_SECONDS) - elapsed)
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
                name="all-user-hourly-audit-and-gpt-review",
            ).start()
            print("[transaction-audit] scheduled hourly; GPT shadow review + MASTER delivery enabled")
    return result


def install():
    _ui.start_menu_thread = start_menu_thread_with_transaction_audit


install()
