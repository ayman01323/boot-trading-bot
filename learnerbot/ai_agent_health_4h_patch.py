from __future__ import annotations

import threading

from . import ai_agent_health_warning_patch as _health

# User-requested cadence: repeat an unresolved AI-agent-health warning no more
# than once every four hours. New/changed unhealthy signatures may still alert
# immediately so a newly failed agent is not hidden for four hours.
WARNING_SECONDS = 4 * 60 * 60
_INSTALLED = False


def _warning_message_4h(snapshot: dict) -> str:
    text = _ORIG_WARNING_MESSAGE(snapshot)
    return text.replace(
        "This warning repeats every 30 minutes while any agent remains unhealthy.",
        "This warning repeats every 4 hours while any agent remains unhealthy.",
    )


def _start_4h(app) -> None:
    with _health._THREAD_LOCK:
        if _health._THREAD_STARTED or not getattr(app, "telegram_bot_token", ""):
            return
        thread = threading.Thread(
            target=_health._watch_loop,
            args=(app,),
            name="ai-agent-health-warning",
            daemon=True,
        )
        thread.start()
        _health._THREAD_STARTED = True
        print("[ai-agent-health] started check=60s warning_repeat=14400s master-role-dynamic=true")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _health.WARNING_SECONDS = WARNING_SECONDS
    _health.warning_message = _warning_message_4h
    _health._start = _start_4h
    _INSTALLED = True


_ORIG_WARNING_MESSAGE = _health.warning_message
install()
