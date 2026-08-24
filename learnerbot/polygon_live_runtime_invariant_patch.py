from __future__ import annotations

import sys

from . import auto_trader as _auto
from . import fast_market as _fast
from . import loss_forensics_runtime_bridge_patch as _bridge
from . import polygon_focus_patch as _polygon
from . import sibot as _sibot
from . import solana_sibot as _sol
from . import polygon_websocket_patch as _evm_ws
from . import solana_websocket_patch as _sol_ws
from . import transaction_audit_worker_patch as _audit_worker


def _polygon_auto_composed() -> bool:
    """Accept Polygon focus directly or underneath Basic Engine v0.

    Do not import the v0 patch here: on normal startup this invariant intentionally
    runs before final_runtime_integrity_patch installs v0. During import-sweep tests
    v0 may already be installed, in which case its preserved inner executor must be
    exactly the audited Polygon focus wrapper.
    """
    if _auto.execute_best_live_opportunity is _polygon._polygon_execute:
        return True
    v0 = sys.modules.get("learnerbot.basic_engine_v0.main_patch")
    return bool(
        v0
        and _auto.execute_best_live_opportunity is getattr(v0, "execute_best_live_opportunity_v0", None)
        and getattr(v0, "_LEGACY_EXECUTE", None) is _polygon._polygon_execute
    )


def _polygon_fast_composed() -> bool:
    if _fast.execute_best_live_opportunity is _polygon._polygon_execute:
        return True
    v0 = sys.modules.get("learnerbot.basic_engine_v0.main_patch")
    return bool(
        v0
        and _fast.execute_best_live_opportunity is getattr(v0, "execute_best_live_opportunity_v0", None)
        and getattr(v0, "_LEGACY_EXECUTE", None) is _polygon._polygon_execute
    )


def install() -> None:
    checks = {
        "polygon_auto_focus": _polygon_auto_composed(),
        "polygon_fast_market_focus": _polygon_fast_composed(),
        "runtime_forensics_bridge": (
            _audit_worker.publish_loss_forensics
            is _bridge.publish_loss_forensics_with_runtime_bridge
        ),
        "evm_websocket_monitor": _sibot.poll_leader_blocks is _evm_ws.poll_leader_blocks_locked,
        "solana_websocket_monitor": _sol.monitor_leaders is _sol_ws.monitor_leaders_locked,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Polygon LIVE/runtime evidence invariant failed: " + ", ".join(failed))
    print("[polygon-live-runtime-invariant] OK audited_hooks=%d" % len(checks))


install()
