from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.rpc_health_audit import audit_rpc_health


def test_runtime_rpc_health_probe() -> None:
    """Explicit-only live probe used through the bounded RUN_TESTS action."""
    if not any("test_runtime_rpc_health_probe.py" in str(arg) for arg in sys.argv):
        pytest.skip("runtime RPC/WSS network probe runs only when explicitly targeted")

    result = audit_rpc_health(timeout_seconds=4.0)
    privacy = result.get("privacy") or {}
    assert privacy.get("rpc_urls_returned") is False
    assert privacy.get("websocket_urls_returned") is False
    assert privacy.get("api_keys_returned") is False
    assert privacy.get("wallet_addresses_returned") is False

    out = Path(__file__).resolve().parents[1] / "data" / "rpc_health_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)
