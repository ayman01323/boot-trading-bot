from __future__ import annotations

import os
import subprocess
import sys


def _run(script: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for name in (
        "ALCHEMY_API_KEY",
        "POLYGON_WS_URL", "POLYGON_ALCHEMY_API_KEY",
        "ARBITRUM_WS_URL", "ARBITRUM_ALCHEMY_API_KEY",
        "BNB_WS_URL", "BNB_ALCHEMY_API_KEY", "BSC_WS_URL", "BSC_ALCHEMY_API_KEY",
        "BASE_WS_URL", "BASE_ALCHEMY_API_KEY",
    ):
        env.pop(name, None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_supported_evm_websocket_chains():
    script = r'''
from learnerbot import polygon_websocket_patch as mod
assert mod._chain_ws_url(137) == "wss://polygon-mainnet.g.alchemy.com/v2/shared"
assert mod._chain_ws_url(42161) == "wss://arb-mainnet.g.alchemy.com/v2/shared"
assert mod._chain_ws_url(56) == "wss://bnb-mainnet.g.alchemy.com/v2/shared"
assert mod._chain_ws_url(8453) == "wss://base-mainnet.g.alchemy.com/v2/shared"
print("EVM_WS_URLS_OK")
'''
    result = _run(script, {"ALCHEMY_API_KEY": "shared"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_URLS_OK" in result.stdout


def test_explicit_and_per_chain_overrides():
    script = r'''
from learnerbot import polygon_websocket_patch as mod
assert mod._chain_ws_url(8453) == "wss://example.invalid/base-secret"
assert mod._chain_ws_url(56) == "wss://example.invalid/bnb-secret"
assert mod._chain_ws_url(42161) == "wss://arb-mainnet.g.alchemy.com/v2/arb-only"
assert mod._chain_ws_url(137) == "wss://polygon-mainnet.g.alchemy.com/v2/shared"
print("EVM_WS_OVERRIDES_OK")
'''
    result = _run(
        script,
        {
            "ALCHEMY_API_KEY": "shared",
            "BASE_WS_URL": "wss://example.invalid/base-secret",
            "BNB_WS_URL": "wss://example.invalid/bnb-secret",
            "ARBITRUM_ALCHEMY_API_KEY": "arb-only",
        },
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_OVERRIDES_OK" in result.stdout
