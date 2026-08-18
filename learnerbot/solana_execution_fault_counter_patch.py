from __future__ import annotations

from contextlib import closing

from . import solana_live_patch as _live
from . import solana_sibot as _sol
from .user_registry import set_user_setting


def fault_count(app, tid) -> int:
    key = f"solana_live_landed_fault_count:{tid}"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        return max(0, _sol._int(_sol._state(conn, key, "0"), 0))


def reset_fault_count(app, tid):
    key = f"solana_live_landed_fault_count:{tid}"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, key, "0")


def record_execution_fault(app, tid, cfg, message):
    """Count every landed-invalid LIVE transaction, including monitor exits."""
    key = f"solana_live_landed_fault_count:{tid}"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        count = max(0, _sol._int(_sol._state(conn, key, "0"), 0)) + 1
        _sol._set_state(conn, key, count)

    threshold = max(1, _sol._int(cfg.get("live_no_output_disable_after"), 2))
    if count >= threshold:
        set_user_setting(
            app.csv_dir, tid, "solana_live_enabled", "false",
            chain_id=_sol.SOLANA_CHAIN_ID,
            description="Automatically disabled after repeated landed-but-invalid Solana executions",
        )
        _live._notify(
            app, tid,
            f"🛑 <b>Solana LIVE automatically disabled</b>\n"
            f"Landed-but-invalid execution faults: <b>{count}</b> (limit {threshold}).\n"
            f"Last fault: <code>{str(message)[:450]}</code>\n"
            "No new Solana LIVE entries or monitored exits will be submitted until LIVE is explicitly re-armed after investigation.",
        )
        return True

    _live._notify(
        app, tid,
        f"⚠️ <b>Solana LIVE execution fault {count}/{threshold}</b>\n"
        f"<code>{str(message)[:500]}</code>\n"
        "The affected signal is not automatically retried. A second landed-invalid execution disables LIVE.",
    )
    return False


def install():
    _live._record_execution_fault = record_execution_fault


install()
