from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import cli as _cli
from . import telegram as _tg
from . import telegram_ui as _ui
from .ai_ops_status import fetch_ai_reviews, master_chat_ids, read_json
from .provider_credit_alerts import alert_rows, mark_delivered, pending_master_ids, status_html


_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_THREAD_LOCK = threading.Lock()
_THREAD_STARTED = False
_STATUS_PATH = "provider-credits/latest_status.json"
_POLL_SECONDS = 5 * 60


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _delivery_state_path(app) -> Path:
    return Path(app.data_dir) / ".ai_provider_credit_telegram_state.json"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _fetch_status(repo_root: Path) -> tuple[dict | None, str]:
    try:
        ok, detail = fetch_ai_reviews(repo_root)
        if not ok:
            return None, detail
        value = read_json(repo_root, _STATUS_PATH)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return None, "provider credit status has an unsupported schema"
        return value, "OK"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:400]


def _successful_chat_ids(result: dict) -> list[str]:
    return [
        str(detail.get("chat_id"))
        for detail in result.get("details") or []
        if detail.get("ok") and str(detail.get("chat_id") or "").strip()
    ]


def _deliver_alerts(app, status: dict) -> None:
    masters = master_chat_ids(app.csv_dir)
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not masters or not token:
        return
    state_path = _delivery_state_path(app)
    state = _read_json(state_path)
    changed = False
    for alert in alert_rows(status):
        missing = pending_master_ids(state, alert["key"], masters)
        if not missing:
            continue
        try:
            result = _tg.send_to_chats(
                token,
                missing,
                alert["text"],
                protect_content=True,
                disable_notification=False,
            )
        except Exception as exc:
            print(f"[provider-credit-alert] send failed: {type(exc).__name__}: {exc}", flush=True)
            continue
        delivered = _successful_chat_ids(result)
        if delivered:
            mark_delivered(state, alert["key"], delivered)
            changed = True
        print(
            f"[provider-credit-alert] provider={alert['provider']} level={alert['level']} "
            f"sent={len(delivered)} pending={max(0, len(missing)-len(delivered))}",
            flush=True,
        )
    if changed:
        state["updated_epoch"] = int(time.time())
        _write_json(state_path, state)


def _watch_loop(app) -> None:
    time.sleep(12)
    while True:
        status, detail = _fetch_status(_repo_root())
        if status is None:
            print(f"[provider-credit-alert] status unavailable: {detail}", flush=True)
        else:
            try:
                _deliver_alerts(app, status)
            except Exception as exc:
                print(f"[provider-credit-alert] monitor failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(_POLL_SECONDS)


def _start_watcher(app) -> None:
    global _THREAD_STARTED
    with _THREAD_LOCK:
        if _THREAD_STARTED or not str(getattr(app, "telegram_bot_token", "") or "").strip():
            return
        thread = threading.Thread(
            target=_watch_loop,
            args=(app,),
            name="provider-credit-telegram-alerts",
            daemon=True,
        )
        thread.start()
        _THREAD_STARTED = True
        print(
            f"[provider-credit-alert] started interval={_POLL_SECONDS}s threshold=80% master-role-dynamic=true",
            flush=True,
        )


def _app_with_provider_credit_alerts():
    app = _PREV_APP()
    _start_watcher(app)
    return app


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if tid is not None and cmd == "/aicredits":
        try:
            _ui._require_master(app, tid)
        except Exception as exc:
            _ui._send(app, tid, f"⚠️ {type(exc).__name__}: {str(exc)[:220]}")
            return
        status, detail = _fetch_status(_repo_root())
        body = status_html(status or {"available": False})
        if status is None:
            body += f"\n\n⚠️ Latest status fetch failed: {str(detail)[:300]}"
        _ui._send(app, tid, body)
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_provider_credit_alert_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _cli._app = _app_with_provider_credit_alerts
    _ui._provider_credit_alert_patch_installed = True


install()
