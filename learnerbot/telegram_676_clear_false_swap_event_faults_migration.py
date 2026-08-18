from __future__ import annotations

import time
from contextlib import closing
from pathlib import Path

from . import cli as _cli
from . import solana_execution_validation_patch  # installs the corrected runtime validation
from . import solana_live_patch as _live
from . import solana_sibot as _sol
from .solana_execution_fault_counter_patch import reset_fault_count

TARGET_TELEGRAM_ID = "6760898817"
MARKER = ".telegram_6760898817_clear_false_swap_event_faults_20260818_v1"
_PREV_APP = _cli._app


def _apply(app) -> None:
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return

    corrected = 0
    remaining_true_faults = 0
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _live._ensure_live_safety_schema(conn)
        rows = conn.execute(
            """SELECT attempt_key,error FROM live_execution_attempts
               WHERE telegram_id=? AND status='LANDED_INVALID_OUTPUT'""",
            (TARGET_TELEGRAM_ID,),
        ).fetchall()
        for row in rows:
            error = str(row["error"] or "")
            only_missing_events = (
                "missing swapEvents" in error
                and "non-positive executed input" not in error
                and "non-positive executed output" not in error
            )
            if only_missing_events:
                conn.execute(
                    """UPDATE live_execution_attempts
                       SET status='EXECUTED_EVENTS_MISSING',updated_at=?
                       WHERE attempt_key=?""",
                    (int(time.time()), str(row["attempt_key"])),
                )
                corrected += 1
        remaining_true_faults = int(conn.execute(
            """SELECT COUNT(*) n FROM live_execution_attempts
               WHERE telegram_id=? AND status='LANDED_INVALID_OUTPUT'""",
            (TARGET_TELEGRAM_ID,),
        ).fetchone()["n"])
        conn.commit()

    # The account-level counter included the now-corrected false positives.  Reset
    # it only when there are no remaining genuine landed-invalid attempt records.
    if corrected and remaining_true_faults == 0:
        reset_fault_count(app, TARGET_TELEGRAM_ID)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "\n".join([
            f"applied_epoch={int(time.time())}",
            f"telegram_id={TARGET_TELEGRAM_ID}",
            f"corrected_missing_swap_event_faults={corrected}",
            f"remaining_true_landed_invalid_faults={remaining_true_faults}",
            "solana_live_reenabled=false",
            "explicit_user_rearm_required=true",
        ]) + "\n",
        encoding="utf-8",
    )
    print(
        f"[telegram-676-fault-recovery] corrected={corrected} "
        f"remaining_true={remaining_true_faults} live_reenabled=false"
    )


def _app_with_false_fault_recovery():
    app = _PREV_APP()
    try:
        _apply(app)
    except Exception as exc:
        print(f"[telegram-676-fault-recovery] ERROR {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_false_fault_recovery
