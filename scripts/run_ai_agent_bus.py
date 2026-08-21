from __future__ import annotations

from scripts.ai_agent_bus_provider_compat import install


install()

from scripts.ai_agent_bus import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
