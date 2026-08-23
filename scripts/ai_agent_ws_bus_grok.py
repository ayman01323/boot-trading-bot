from __future__ import annotations

from scripts import ai_agent_ws_bus as _base

# Keep the audited broker implementation unchanged; extend the real recipient
# set with Grok and add one non-worker client identity for the MASTER interactive
# chat. MASTER may register and send to agents, but it is never an AI recipient
# and is never included in Council/broadcast fan-out.
_base.AGENTS = set(_base.AGENTS) | {"grok"}
CLIENT_IDENTITIES = set(_base.AGENTS) | {"master"}
_BASE_REGISTER = _base.Broker._register


async def _register_with_master(self, ws, data) -> None:
    identity = str(data.get("agent") or "").strip().lower()
    if identity != "master":
        await _BASE_REGISTER(self, ws, data)
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


_base.Broker._register = _register_with_master

AGENTS = _base.AGENTS
BusError = _base.BusError
Store = _base.Store
run = _base.run


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
