from __future__ import annotations

import sys
import threading
import time

from .cli import main
from .deploy_timer import install_telegram_patch
from .master_admins import ensure_master_admins

# Ensure the operator-authorised Telegram administrators are present in the
# local user registry before the interactive Telegram menu starts.
try:
    admins = ensure_master_admins()
    print(f"[telegram-master-admins] active={','.join(admins)}", flush=True)
except Exception as exc:
    print(f"[telegram-master-admins-error] {type(exc).__name__}: {exc}", flush=True)

# Add the MASTER-only /deploytimer command and inline timer controls before the
# Telegram polling thread starts. This does not expose arbitrary shell commands.
install_telegram_patch()


def _send_challenge_target_test_after_startup():
    """Send the one-shot Telegram delivery test shortly after learnerbot starts."""
    time.sleep(8)
    try:
        from .config import AppSettings
        from .challenge_alerts import send_target_test_once
        result = send_target_test_once(AppSettings.load())
        print(f"[challenge-target-test] {result}", flush=True)
    except Exception as exc:
        print(f"[challenge-target-test-error] {type(exc).__name__}: {exc}", flush=True)


def _run_adaptive_challenge_controller():
    """Run the bounded adaptive challenge controller inside the learnerbot service.

    It only changes whitelisted discovery/search-breadth settings. It cannot alter
    capital, slippage, minimum-profit, gas bidding, signing, or final simulation
    safety gates. The controller exits automatically when the challenge ends.
    """
    time.sleep(12)
    try:
        from scripts.adaptive_strategy_controller import main as adaptive_main
        rc = adaptive_main()
        print(f"[adaptive-strategy-controller] exit={rc}", flush=True)
    except Exception as exc:
        print(f"[adaptive-strategy-controller-error] {type(exc).__name__}: {exc}", flush=True)


if len(sys.argv) > 1 and sys.argv[1] == "run":
    threading.Thread(
        target=_send_challenge_target_test_after_startup,
        name="challenge-target-test",
        daemon=True,
    ).start()
    threading.Thread(
        target=_run_adaptive_challenge_controller,
        name="adaptive-strategy-controller",
        daemon=True,
    ).start()

raise SystemExit(main())
