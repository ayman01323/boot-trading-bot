from __future__ import annotations

import sys
import threading
import time

from .cli import main
from .deploy_timer import install_telegram_patch

# Add the MASTER-only /deploytimer command and inline timer controls before the
# Telegram polling thread starts. This does not expose arbitrary shell commands.
install_telegram_patch()


def _send_challenge_target_test_after_startup():
    """Send the one-shot Telegram delivery test shortly after learnerbot starts.

    The delay avoids competing with initial Telegram/menu setup. The helper itself
    is fail-closed and only marks success when all intended recipients receive it.
    """
    time.sleep(8)
    try:
        from .config import AppSettings
        from .challenge_alerts import send_target_test_once
        result = send_target_test_once(AppSettings.load())
        print(f"[challenge-target-test] {result}", flush=True)
    except Exception as exc:
        print(f"[challenge-target-test-error] {type(exc).__name__}: {exc}", flush=True)


if len(sys.argv) > 1 and sys.argv[1] == "run":
    threading.Thread(
        target=_send_challenge_target_test_after_startup,
        name="challenge-target-test",
        daemon=True,
    ).start()

raise SystemExit(main())
