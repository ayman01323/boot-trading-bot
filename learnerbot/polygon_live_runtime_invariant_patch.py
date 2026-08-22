from __future__ import annotations

from . import auto_trader as _auto
from . import fast_market as _fast
from . import loss_forensics_runtime_bridge_patch as _bridge
from . import polygon_focus_patch as _polygon
from . import sibot as _sibot
from . import solana_sibot as _sol
# Load only after the EVM/Solana reliability patches have installed their proven
# failed-block/signature retry paths. WebSockets wake those same monitors early;
# they do not replace HTTP RPC validation or execution safeguards.
from . import polygon_websocket_patch as _evm_ws
from . import solana_websocket_patch as _sol_ws
from . import transaction_audit_worker_patch as _audit_worker


def install() -> None:
    checks = {
        "polygon_auto_focus": _auto.execute_best_live_opportunity is _polygon._polygon_execute,
        "polygon_fast_market_focus": _fast.execute_best_live_opportunity is _polygon._polygon_execute,
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
