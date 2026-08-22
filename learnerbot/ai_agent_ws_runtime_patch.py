from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
from pathlib import Path

_STARTED = False
_THREAD: threading.Thread | None = None
HOST = "127.0.0.1"
PORT = 8765
DB_PATH = "/var/tmp/boot/ai_agent_bus.sqlite3"
STATUS_PATH = "/var/tmp/boot/ai_agent_ws_status.json"
AGENTS = ("gpt", "claude", "gemini", "deepseek", "copilot")


def _port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.25):
            return True
    except OSError:
        return False


def _is_runtime_run_command() -> bool:
    # The production service runs `python -m learnerbot run`. Avoid starting a
    # daemon sidecar for short administrative commands such as `chains` or
    # `telegram-test`, and avoid unnecessary provider worker connections in tests.
    return len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "run"


def _write_status(state: str, detail: str = "") -> None:
    try:
        import json
        path = Path(STATUS_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "schema_version": 1,
            "state": state,
            "detail": str(detail or "")[:500],
            "host": HOST,
            "port": PORT,
            "workers": list(AGENTS),
            "pid": os.getpid(),
            "updated_epoch": int(time.time()),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        pass


async def _run_embedded() -> None:
    from scripts.ai_agent_ws_bus import run as run_broker
    from scripts.ai_agent_ws_worker import run as run_worker

    broker = asyncio.create_task(run_broker(HOST, PORT, DB_PATH, os.environ.get("AI_AGENT_BUS_TOKEN", "")))
    await asyncio.sleep(0.35)
    if broker.done():
        await broker
    workers = [
        asyncio.create_task(run_worker(agent, f"ws://{HOST}:{PORT}", os.environ.get("AI_AGENT_BUS_TOKEN", "")))
        for agent in AGENTS
    ]
    _write_status("ACTIVE", "embedded learnerbot WebSocket broker and persistent workers started")
    print("[ai-agent-ws] embedded broker active with gpt/claude/gemini/deepseek/copilot workers", flush=True)
    await asyncio.gather(broker, *workers)


def _thread_main() -> None:
    try:
        asyncio.run(_run_embedded())
    except Exception as exc:
        _write_status("FAILED", f"{type(exc).__name__}: {exc}")
        print(f"[ai-agent-ws] sidecar failed: {type(exc).__name__}: {exc}", flush=True)


def install() -> None:
    global _STARTED, _THREAD
    if _STARTED:
        return
    _STARTED = True
    if not _is_runtime_run_command():
        return
    if str(os.environ.get("AI_AGENT_WS_AUTOSTART", "1")).strip().lower() in {"0", "false", "no", "off"}:
        _write_status("DISABLED", "AI_AGENT_WS_AUTOSTART disabled")
        return
    if _port_open():
        _write_status("EXTERNAL", "port already served; embedded sidecar not started")
        print("[ai-agent-ws] existing local broker detected; embedded sidecar skipped", flush=True)
        return
    _THREAD = threading.Thread(target=_thread_main, name="ai-agent-ws-sidecar", daemon=True)
    _THREAD.start()
    _write_status("STARTING", "embedded learnerbot sidecar thread launched")


install()

# Observational only: install high-resolution Solana LIVE timing after the audited
# trading invariant. It measures existing calls and never changes strategy, LIVE,
# signing, reserve, simulation, liquidity or execution safety decisions.
from . import solana_execution_latency_patch  # noqa: E402,F401

# MASTER Telegram change requests depend on this local bus. Import the final
# command patch after the bus runtime has installed so /aichange can route to all
# adviser workers without GitHub mailbox polling.
from . import telegram_master_change_patch  # noqa: E402,F401

# EVM historical leader reconstruction uses only the complete Alchemy HTTP URLs
# stored in VPS-local rpc_endpoints.csv. No ALCHEMY_API_KEY or ETHERSCAN_API_KEY
# environment variable is required by this path.
from . import sibot_alchemy_history_patch  # noqa: E402,F401

# Alchemy applies compute-units-per-second throughput limits. Install bounded
# Retry-After/exponential-backoff handling and smaller read-only RPC batches before
# the chain-specific internal-flow layer uses the shared provider helpers.
from . import sibot_alchemy_rate_limit_patch  # noqa: E402,F401

# Arbitrum and BNB require trace-based internal native-flow reconstruction because
# Alchemy Transfers can return a valid empty internal result on those networks.
# Keep this layer after the base Alchemy provider so it only replaces the wallet
# refresh implementation and leaves provider selection/history gating untouched.
from . import sibot_alchemy_internal_trace_patch  # noqa: E402,F401

# Retry transient provider throttles promptly and serialise history backfills
# across EVM chains so multiple workers do not burst the same Alchemy account.
from . import sibot_alchemy_retry_queue_patch  # noqa: E402,F401

# Final operational truth layer: expose the exact reason no trade is occurring
# without changing LIVE scope, thresholds, capital, signing or any safety gate.
from . import telegram_trade_blocker_health_patch  # noqa: E402,F401

# Replace the legacy global Etherscan dependency status with per-chain Alchemy
# history-provider readiness.
from . import trade_blocker_alchemy_history_patch  # noqa: E402,F401

# Sanitise any upstream HTTP error before it reaches Telegram or the redacted
# health JSON; provider URLs can contain private API credentials.
from . import trade_blocker_secret_redaction_patch  # noqa: E402,F401

# Add read-only Solana wallet funding, platform amount-profit and selected-leader
# edge truth, including gates that reject before the older decision logger runs.
from . import solana_trade_gate_truth_patch  # noqa: E402,F401
