from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import cli as _cli
from . import telegram as _tg
from .ai_ops_status import fetch_ai_reviews, master_chat_ids, read_json

_PREV_APP = _cli._app
_THREAD_LOCK = threading.Lock()
_THREAD_STARTED = False
CHECK_SECONDS = 60
MAX_EVENT_AGE_SECONDS = 15 * 60
BUS_SNAPSHOT_PATH = "github/ai-agent-bus/latest.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _state_path(app) -> Path:
    return Path(app.data_dir) / ".ai_bus_telegram_alert_state.json"


def _clean(value, limit: int = 700) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\b(sk|sess)-[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
    text = re.sub(r"\bAIza[A-Za-z0-9_-]{20,}\b", "<redacted>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _message(snapshot: dict) -> str:
    providers = [str(x).upper() for x in snapshot.get("providers") or [] if str(x).strip()]
    provider_text = ", ".join(providers) or str(snapshot.get("to") or "AGENT").upper()
    status = str(snapshot.get("status") or "UNKNOWN").upper()
    icon = "✅" if status == "COMPLETED" else ("⚠️" if status == "BLOCKED" else "ℹ️")
    lines = [
        "🤖 AI AGENT MESSAGE",
        f"Agent: {provider_text}",
        f"Status: {icon} {status}",
        f"Message ID: {_clean(snapshot.get('message_id'), 120)}",
    ]
    preview = _clean(snapshot.get("preview"), 650)
    if preview:
        lines += ["", f"Reply: {preview}"]
    return "\n".join(lines)


def _watch_loop(app) -> None:
    time.sleep(15)
    state_path = _state_path(app)
    state = _load_state(state_path)
    while True:
        try:
            ok, detail = fetch_ai_reviews(_repo_root(), timeout=20)
            if not ok:
                print(f"[ai-bus-telegram] ai-reviews fetch failed: {detail}")
                time.sleep(CHECK_SECONDS)
                continue

            snapshot = read_json(_repo_root(), BUS_SNAPSHOT_PATH) or {}
            message_id = str(snapshot.get("message_id") or "").strip()
            last_message_id = str(state.get("last_message_id") or "").strip()
            try:
                generated_epoch = int(snapshot.get("generated_epoch") or 0)
            except (TypeError, ValueError):
                generated_epoch = 0
            age = max(0, int(time.time()) - generated_epoch) if generated_epoch else 10**9

            if message_id and message_id != last_message_id and age <= MAX_EVENT_AGE_SECONDS:
                masters = master_chat_ids(Path(app.csv_dir))
                token = str(getattr(app, "telegram_bot_token", "") or "").strip()
                if token and masters:
                    result = _tg.send_to_chats(
                        token,
                        masters,
                        _message(snapshot),
                        disable_notification=False,
                        protect_content=True,
                    )
                    if int(result.get("sent_chats", 0)) > 0:
                        state = {
                            "last_message_id": message_id,
                            "last_status": str(snapshot.get("status") or ""),
                            "last_sent_epoch": int(time.time()),
                        }
                        _save_state(state_path, state)
                        print(
                            f"[ai-bus-telegram] notified message_id={message_id} "
                            f"masters={len(masters)} sent={int(result.get('sent_chats', 0))}"
                        )
            elif message_id and not last_message_id and age > MAX_EVENT_AGE_SECONDS:
                # Seed stale state after first deployment/restart so an old reply is not replayed.
                state = {
                    "last_message_id": message_id,
                    "last_status": str(snapshot.get("status") or ""),
                    "last_sent_epoch": 0,
                }
                _save_state(state_path, state)
        except Exception as exc:
            print(f"[ai-bus-telegram] {type(exc).__name__}: {exc}")
        time.sleep(CHECK_SECONDS)


def _start(app) -> None:
    global _THREAD_STARTED
    with _THREAD_LOCK:
        if _THREAD_STARTED or not getattr(app, "telegram_bot_token", ""):
            return
        thread = threading.Thread(
            target=_watch_loop,
            args=(app,),
            name="ai-bus-telegram-alerts",
            daemon=True,
        )
        thread.start()
        _THREAD_STARTED = True
        print("[ai-bus-telegram] started interval=60s master-role-dynamic=true")


def _app_with_ai_bus_alerts():
    app = _PREV_APP()
    try:
        _start(app)
    except Exception as exc:
        print(f"[ai-bus-telegram-start] {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_ai_bus_alerts
