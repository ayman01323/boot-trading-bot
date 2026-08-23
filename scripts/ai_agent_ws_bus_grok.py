from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scripts import ai_agent_ws_bus as _base

# Keep the audited broker implementation unchanged; extend the real recipient
# set with Grok and add one non-worker client identity for the MASTER interactive
# chat. MASTER may register and send to agents, but it is never an AI recipient
# and is never included in Council/broadcast fan-out.
_base.AGENTS = set(_base.AGENTS) | {"grok"}
CLIENT_IDENTITIES = set(_base.AGENTS) | {"master"}
_BASE_REGISTER = _base.Broker._register
_BASE_REMOVE = _base.Broker._remove
_CONNECTION_STATUS_PATH = Path("/var/tmp/boot/ai_agent_ws_connections.json")


def _write_connection_truth(broker) -> None:
    """Publish the broker's actual registered-recipient set for Telegram health.

    The status carries the current learnerbot PID. The Telegram renderer only
    trusts a file whose PID matches its own process, so a stale file from a dead
    service can never make agents appear healthy after a restart.
    """
    try:
        connected = sorted(
            agent
            for agent in _base.AGENTS
            if bool(getattr(broker, "connections", {}).get(agent))
        )
        value = {
            "schema_version": 1,
            "pid": os.getpid(),
            "updated_epoch": int(time.time()),
            "connected_agents": connected,
            "expected_agents": sorted(_base.AGENTS),
        }
        _CONNECTION_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CONNECTION_STATUS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, _CONNECTION_STATUS_PATH)
    except Exception:
        # Observability must never interfere with message delivery.
        pass


async def _register_with_master(self, ws, data) -> None:
    identity = str(data.get("agent") or "").strip().lower()
    if identity != "master":
        await _BASE_REGISTER(self, ws, data)
        _write_connection_truth(self)
        return
    if self.token and str(data.get("token") or "") != self.token:
        raise _base.BusError("authentication failed")
    async with self.lock:
        old = self.reverse.get(ws)
        if old:
            self.connections[old].discard(ws)
        self.reverse[ws] = identity
        self.connections[identity].add(ws)
    await self._send(ws, {"type": "registered", "agent": identity})
    # MASTER is sender-only, so it has no inbound message queue. It can still
    # reconnect to receive a durable reply from an earlier MASTER -> agent turn.
    await self._deliver_pending_replies(identity)
    _write_connection_truth(self)


async def _remove_with_connection_truth(self, ws) -> None:
    await _BASE_REMOVE(self, ws)
    _write_connection_truth(self)


_base.Broker._register = _register_with_master
_base.Broker._remove = _remove_with_connection_truth

AGENTS = _base.AGENTS
BusError = _base.BusError
Store = _base.Store
run = _base.run


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
