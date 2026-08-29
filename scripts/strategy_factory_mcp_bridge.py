from __future__ import annotations

import os

from mcp.server import MCPServer

try:
    from scripts.strategy_factory_mcp_core import send_to_gpt as _send_to_gpt
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from strategy_factory_mcp_core import send_to_gpt as _send_to_gpt


mcp = MCPServer(
    "Strategy Factory GPT Bridge",
    instructions=(
        "Communication-only bridge from Gemini to the Strategy Factory GPT worker. "
        "It cannot trade, deploy, restart services, alter LIVE/risk/capital, access wallets/signing, "
        "or modify repository files. Use send_to_gpt only when the user explicitly asks GPT a question."
    ),
)


@mcp.tool()
async def send_to_gpt(message: str) -> dict[str, object]:
    """Send one text-only message from Gemini to GPT and return the correlated GPT reply.

    The bridge forces communication-only handling. It does not expose shell commands,
    repository mutation, deployment, trading, wallet, signing, LIVE, risk or capital tools.
    """

    return await _send_to_gpt(message)


def _listen_settings() -> tuple[str, int, str]:
    host = os.environ.get("STRATEGY_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.environ.get("STRATEGY_MCP_PORT", "8790")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("STRATEGY_MCP_PORT must be an integer") from exc
    if port < 1024 or port > 65535:
        raise ValueError("STRATEGY_MCP_PORT must be between 1024 and 65535")

    path = os.environ.get("STRATEGY_MCP_PATH", "/mcp").strip() or "/mcp"
    if not path.startswith("/") or "?" in path or "#" in path or " " in path:
        raise ValueError("STRATEGY_MCP_PATH must be a simple absolute URL path")

    # The MCP process must remain private. A public HTTPS reverse proxy / OAuth
    # resource-server layer is the only supported way to expose it externally.
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError(
            "Refusing non-loopback bind. Keep the Strategy Factory MCP bridge on localhost "
            "and expose it only through an authenticated HTTPS reverse proxy."
        )
    return host, port, path


def main() -> None:
    host, port, path = _listen_settings()
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
