from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

from . import ai_agent_health_warning_patch as _health
from . import telegram as _tg


def _lock_path(app) -> Path:
    state = _health._state_path(app)
    return state.with_suffix(state.suffix + ".lock")


@contextmanager
def _exclusive_health_lock(app):
    """Serialize health notification decisions across overlapping bot processes."""
    path = _lock_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _notification_cycle(app, snapshot: dict, *, now: int | None = None) -> str:
    """Send at most one warning/recovery for this state across all bot processes.

    The state is intentionally reloaded *after* acquiring the OS lock.  This is
    what prevents two overlapping service processes from both observing stale
    state and sending the same Telegram warning within seconds of each other.
    """
    current = int(now or time.time())
    state_path = _health._state_path(app)
    unhealthy = _health.unhealthy_rows(snapshot)
    signature = _health.health_signature(snapshot)

    with _exclusive_health_lock(app):
        sent = _health._load_state(state_path)
        last_signature = str(sent.get("last_signature") or "")
        last_sent = int(sent.get("last_sent_epoch") or 0)
        had_unhealthy = bool(sent.get("had_unhealthy"))
        masters = _health.master_chat_ids(Path(app.csv_dir))
        token = str(getattr(app, "telegram_bot_token", "") or "").strip()

        if unhealthy:
            due = signature != last_signature or current - last_sent >= _health.WARNING_SECONDS
            if due and token and masters:
                _tg.send_to_chats(
                    token,
                    masters,
                    _health.warning_message(snapshot),
                    disable_notification=False,
                )
                _health._save_state(
                    state_path,
                    {
                        "had_unhealthy": True,
                        "last_signature": signature,
                        "last_sent_epoch": current,
                        "unhealthy_count": len(unhealthy),
                    },
                )
                return "WARNING"
            return "NONE"

        if had_unhealthy:
            if token and masters:
                _tg.send_to_chats(
                    token,
                    masters,
                    _health.recovery_message(snapshot),
                    disable_notification=False,
                )
            _health._save_state(
                state_path,
                {
                    "had_unhealthy": False,
                    "last_signature": signature,
                    "last_sent_epoch": current,
                    "unhealthy_count": 0,
                },
            )
            return "RECOVERY"

        return "NONE"


def _watch_loop(app) -> None:
    time.sleep(20)
    while True:
        try:
            ok, detail = _health.fetch_ai_reviews(_health._repo_root(), timeout=20)
            if not ok:
                print(f"[ai-agent-health] ai-reviews fetch failed: {detail}")
                time.sleep(_health.CHECK_SECONDS)
                continue
            now = int(time.time())
            snapshot = _health.build_health_snapshot(_health._repo_root(), now=now)
            _notification_cycle(app, snapshot, now=now)
        except Exception as exc:
            print(f"[ai-agent-health] {type(exc).__name__}: {exc}")
        time.sleep(_health.CHECK_SECONDS)


def install() -> None:
    # _health._start resolves this module global at thread-start time, so replacing
    # it before cli.main() starts is sufficient even though the original patch
    # wrapped _cli._app earlier in import composition.
    _health._watch_loop = _watch_loop
    _health._cross_process_dedupe_installed = True


install()
