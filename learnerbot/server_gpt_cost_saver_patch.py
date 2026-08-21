from __future__ import annotations

import os
from pathlib import Path

from . import profit_control_loop_patch as _control
from . import transaction_audit_worker_patch as _worker

_INSTALLED = False


def _bool(value, default=False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def server_hourly_gpt_enabled(app) -> bool:
    """Legacy server GPT is opt-in; gated Strategy Lab is the paid AI layer."""
    env = os.getenv("SERVER_HOURLY_GPT_ENABLED", "").strip()
    if env:
        return _bool(env, False)
    try:
        cfg = app.general()
        return _bool((cfg or {}).get("server_hourly_gpt_enabled", "false"), False)
    except Exception:
        return False


def skipped_server_gpt_result() -> dict:
    return {
        "ok": True,
        "skipped": True,
        "mode": "SHADOW_ONLY",
        "live_auto_deploy": False,
        "cost_control": "REDUNDANT_SERVER_GPT_DISABLED",
        "review": {
            "status": "SKIPPED_COST_CONTROL",
            "recommended_action": "KEEP_CURRENT_LIVE_SETTINGS",
            "executive_summary": (
                "Hourly deterministic audit and profit-control processing continued without a paid "
                "server GPT call. Gated Strategy Lab is the paid multi-agent review layer."
            ),
            "findings": [],
        },
    }


def _cost_controlled_original_review(app, zip_path):
    if server_hourly_gpt_enabled(app):
        return _control._server_gpt_original_review(app, zip_path)
    return skipped_server_gpt_result()


def _send_gpt_review_message_cost_aware(app, chat_id: str, result: dict) -> None:
    if bool((result or {}).get("skipped")):
        return
    return _worker._server_gpt_original_send_message(app, chat_id, result)


def _send_audit_document_cost_aware(app, chat_id: str, zip_path: str, summary: dict) -> None:
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
        "Hourly audit/learning remains active. Paid Strategy Lab AI runs only when the cost gate allows it."
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with path.open("rb") as fh:
        response = _worker.requests.post(
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


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if not hasattr(_control, "_server_gpt_original_review"):
        _control._server_gpt_original_review = _control._ORIGINAL_GPT_REVIEW
    if not hasattr(_worker, "_server_gpt_original_send_message"):
        _worker._server_gpt_original_send_message = _worker.send_gpt_review_message
    if not hasattr(_worker, "_server_gpt_original_send_document"):
        _worker._server_gpt_original_send_document = _worker.send_audit_document

    # The existing deterministic profit-control wrapper remains exactly where it
    # is in the runtime stack. Only its inner legacy paid GPT call is replaced.
    _control._ORIGINAL_GPT_REVIEW = _cost_controlled_original_review
    _worker.send_gpt_review_message = _send_gpt_review_message_cost_aware
    _worker.send_audit_document = _send_audit_document_cost_aware
    _INSTALLED = True
    print(
        "[server-gpt-cost-saver] legacy_hourly_gpt=OPT_IN strategy_lab_paid_ai=MATERIAL_CHANGE_GATED "
        "hourly_audit=true deterministic_profit_control=true"
    )


install()
