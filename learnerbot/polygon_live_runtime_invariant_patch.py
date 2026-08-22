from __future__ import annotations

from . import auto_trader as _auto
from . import fast_market as _fast
from . import loss_forensics_runtime_bridge_patch as _bridge
from . import polygon_focus_patch as _polygon
# Load only after sibot_evm_worker_reliability_patch has installed the proven
# failed-block/receipt retry path.  WebSocket wakes that same monitor early;
# it does not replace its HTTP RPC validation/fallback semantics.
from . import polygon_websocket_patch as _polygon_ws  # noqa: F401
from . import transaction_audit_worker_patch as _audit_worker


def install() -> None:
    checks = {
        "polygon_auto_focus": _auto.execute_best_live_opportunity is _polygon._polygon_execute,
        "polygon_fast_market_focus": _fast.execute_best_live_opportunity is _polygon._polygon_execute,
        "runtime_forensics_bridge": (
            _audit_worker.publish_loss_forensics
            is _bridge.publish_loss_forensics_with_runtime_bridge
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Polygon LIVE/runtime evidence invariant failed: " + ", ".join(failed))
    print("[polygon-live-runtime-invariant] OK audited_hooks=%d" % len(checks))


install()
