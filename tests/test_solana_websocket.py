from __future__ import annotations

import os
import subprocess
import sys


def _run(script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for name in ("SOLANA_WS_URL", "HELIUS_WS_URL", "HELIUS_API_KEY"):
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_solana_ws_url_resolution():
    script = r'''
from learnerbot import solana_websocket_patch as mod
assert mod._rpc_to_ws("https://mainnet.helius-rpc.com/?api-key=x") == "wss://mainnet.helius-rpc.com/?api-key=x"
assert mod._rpc_to_ws("http://127.0.0.1:8899") == "ws://127.0.0.1:8899"
assert mod._rpc_to_ws("wss://example.invalid/ws") == "wss://example.invalid/ws"
assert mod._solana_ws_url(object()) == "wss://mainnet.helius-rpc.com/?api-key=helius-key"
print("SOLANA_WS_URL_OK")
'''
    result = _run(script, {"HELIUS_API_KEY": "helius-key"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SOLANA_WS_URL_OK" in result.stdout


def test_explicit_solana_ws_url_wins():
    script = r'''
from learnerbot import solana_websocket_patch as mod
assert mod._solana_ws_url(object()) == "wss://example.invalid/solana-secret"
print("SOLANA_WS_OVERRIDE_OK")
'''
    result = _run(
        script,
        {
            "HELIUS_API_KEY": "shared",
            "SOLANA_WS_URL": "wss://example.invalid/solana-secret",
        },
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SOLANA_WS_OVERRIDE_OK" in result.stdout


def test_evm_alchemy_supported_chains_use_filtered_leader_streams():
    script = r'''
from learnerbot import polygon_websocket_patch as mod

params, mode = mod._subscription_request(
    137,
    "wss://polygon-mainnet.g.alchemy.com/v2/example",
    ["0xabc", "0xdef"],
)
assert mode == "leader_mined_transactions"
assert params[0] == "alchemy_minedTransactions"
assert params[1]["hashesOnly"] is True
assert params[1]["includeRemoved"] is False
assert params[1]["addresses"] == [{"from": "0xabc"}, {"from": "0xdef"}]

params, mode = mod._subscription_request(
    42161,
    "wss://arb-mainnet.g.alchemy.com/v2/example",
    ["0xabc"],
)
assert mode == "leader_mined_transactions"
assert params[0] == "alchemy_minedTransactions"

for chain_id in (56, 8453):
    params, mode = mod._subscription_request(
        chain_id,
        "wss://example.g.alchemy.com/v2/example",
        ["0xabc"],
    )
    assert mode == "new_heads"
    assert params == ["newHeads"]

assert mod._notification_should_wake(
    {"method": "eth_subscription", "params": {"result": {"removed": False, "transaction": {"hash": "0x1"}}}},
    "leader_mined_transactions",
)
assert not mod._notification_should_wake(
    {"method": "eth_subscription", "params": {"result": {"removed": True, "transaction": {"hash": "0x1"}}}},
    "leader_mined_transactions",
)
print("EVM_FILTERED_WS_OK")
'''
    result = _run(script)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_FILTERED_WS_OK" in result.stdout


def test_solana_periodic_fallback_slows_only_while_ws_is_healthy():
    script = r'''
import time
from learnerbot import solana_sibot as sol
from learnerbot import solana_worker_reliability_patch as workers
from learnerbot import solana_websocket_patch as ws

cfg = {"leader_poll_seconds": "5", "leader_ws_healthy_fallback_seconds": "10"}
ws._mark_ws_unhealthy()
assert workers._leader_sleep_seconds(cfg) == 5
ws._mark_ws_healthy()
assert float(sol._leader_ws_healthy_until) > time.monotonic()
assert workers._leader_sleep_seconds(cfg) == 10
ws._mark_ws_unhealthy()
assert workers._leader_sleep_seconds(cfg) == 5
print("SOLANA_ADAPTIVE_FALLBACK_OK")
'''
    result = _run(script)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SOLANA_ADAPTIVE_FALLBACK_OK" in result.stdout


def test_websocket_runtime_wraps_reliable_monitors():
    script = r'''
from learnerbot import sibot as sibot
from learnerbot import solana_sibot as sol
from learnerbot import sibot_evm_worker_reliability_patch as evm_reliable
from learnerbot import solana_worker_reliability_patch as sol_workers
from learnerbot import solana_leader_cursor_reliability_patch as sol_cursor
from learnerbot import polygon_websocket_patch as evm_ws
from learnerbot import solana_websocket_patch as sol_ws

assert evm_ws._ORIGINAL_POLL is evm_reliable.poll_leader_blocks_reliable
assert sibot.poll_leader_blocks is evm_ws.poll_leader_blocks_locked
assert sol_ws._ORIGINAL_MONITOR_LEADERS is sol_cursor.monitor_leaders_reliable
assert sol.monitor_leaders is sol_ws.monitor_leaders_locked
assert sol_ws._ORIGINAL_START_WORKERS is sol_workers.start_workers_reliable
assert sol.start_workers is sol_ws.start_workers_with_solana_ws
print("WEBSOCKET_RUNTIME_COMPOSITION_OK")
'''
    result = _run(script)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "WEBSOCKET_RUNTIME_COMPOSITION_OK" in result.stdout
