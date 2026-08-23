from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.rpc_health_audit import audit_rpc_health


def _atomic_json(path: Path, value: object, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if compact:
        text = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


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

    data_dir = Path(__file__).resolve().parents[1] / "data"
    _atomic_json(data_dir / "rpc_health_latest.json", result)
    _atomic_json(
        data_dir / "rpc_health_rpc_compact.json",
        {
            "generated_epoch": result.get("generated_epoch"),
            "rpc": result.get("rpc") or [],
            "summary": result.get("summary") or {},
        },
        compact=True,
    )
