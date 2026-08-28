from __future__ import annotations

from . import ai_agent_health_warning_patch as _health

# Automatic AI-agent-health Telegram warnings were explicitly disabled by the
# owner on 2026-08-28. Keep the health collectors and manual AI report surfaces
# intact; only prevent the background watcher from starting and sending the
# recurring unsolicited dashboard/warning message.
WARNING_SECONDS = 4 * 60 * 60
AUTOMATIC_TELEGRAM_HEALTH_ALERTS_ENABLED = False
_INSTALLED = False


def _warning_message_disabled(snapshot: dict) -> str:
    text = _ORIG_WARNING_MESSAGE(snapshot)
    text = text.replace(
        "This warning repeats every 30 minutes while any agent remains unhealthy.",
        "Automatic Telegram AI-agent-health warnings are disabled.",
    )
    text = text.replace(
        "This warning repeats every 4 hours while any agent remains unhealthy.",
        "Automatic Telegram AI-agent-health warnings are disabled.",
    )
    return text


def _start_disabled(_app) -> None:
    # Deliberately do not create _health._watch_loop. A service restart therefore
    # terminates any previously-running watcher and it cannot be recreated.
    print("[ai-agent-health] automatic Telegram warnings disabled by owner")
    return None


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _health.WARNING_SECONDS = WARNING_SECONDS
    _health.warning_message = _warning_message_disabled
    _health._start = _start_disabled
    _INSTALLED = True


_ORIG_WARNING_MESSAGE = _health.warning_message
install()
