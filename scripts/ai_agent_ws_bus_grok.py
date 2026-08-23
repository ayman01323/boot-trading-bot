from __future__ import annotations

from scripts import ai_agent_ws_bus as _base

# Keep the audited broker implementation unchanged; extend only its accepted
# addressing set before entering the normal broker main/run paths.
_base.AGENTS = set(_base.AGENTS) | {"grok"}

AGENTS = _base.AGENTS
BusError = _base.BusError
Store = _base.Store
run = _base.run


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
