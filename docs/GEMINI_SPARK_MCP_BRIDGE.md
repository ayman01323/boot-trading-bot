# Gemini web → Strategy Factory GPT bridge

## Purpose

This bridge lets an eligible Gemini Spark custom Connected App call one bounded MCP tool:

`send_to_gpt(message)`

The tool relays the text as `gemini → gpt` through the existing Strategy Factory WebSocket bus and returns the correlated GPT reply with delivery/acknowledgement status.

It is communication-only. It intentionally exposes no shell, file mutation, Git, deploy, restart, trading, LIVE/ARMED, risk/capital, wallet or signing tool.

## Google eligibility gate

Google's current Gemini documentation (checked 2026-08-23) says custom MCP Connected Apps are available only inside Gemini Spark and currently require an eligible personal Google account, English, Keep Activity on, age 18+, and US availability. This is a Google product gate, not a limitation of this repository.

Current Google setup page:

https://support.google.com/gemini/answer/17209137

Do not weaken this bridge to work around a Google account/region restriction.

## Local architecture

```text
Gemini Spark custom app
        |
        | HTTPS + OAuth 2.1 (required before public exposure)
        v
public MCP resource-server / reverse proxy
        |
        | localhost only
        v
127.0.0.1:8790/mcp
strategy_factory_mcp_bridge.py
        |
        | ws://127.0.0.1:8765
        v
Strategy Factory broker
        |
        v
GPT worker
```

The Python MCP process itself refuses a non-loopback bind. Public HTTPS and OAuth must terminate in a separate, reviewed front door. Never expose port 8790 directly to the Internet.

## Files

- `scripts/strategy_factory_mcp_core.py` — validates/wraps external text and uses the canonical Strategy Factory transport.
- `scripts/strategy_factory_mcp_bridge.py` — MCP Streamable HTTP server with the single `send_to_gpt` tool.
- `requirements-mcp-bridge.txt` — isolated MCP dependencies; deliberately separate from the trading bot requirements.
- `tests/test_strategy_factory_mcp_bridge.py` — routing and safety tests.

## Local smoke test

MCP v2 requires Python 3.10+. This VPS already has Python 3.11, so use it explicitly instead of the older default `python3`.

Create a separate environment; do not install MCP packages into the trading bot venv:

```bash
python3.11 -m venv /opt/strategy-factory-mcp-bridge-venv
/opt/strategy-factory-mcp-bridge-venv/bin/pip install -r requirements-mcp-bridge.txt

STRATEGY_MCP_HOST=127.0.0.1 \
STRATEGY_MCP_PORT=8790 \
/opt/strategy-factory-mcp-bridge-venv/bin/python scripts/strategy_factory_mcp_bridge.py
```

The MCP endpoint is then:

`http://127.0.0.1:8790/mcp`

It can be tested locally with an MCP Inspector/client. A real tool call requires the Strategy Factory broker and GPT worker to be running.

## Public deployment requirements

Do not make the endpoint public until all of these are true:

1. A dedicated HTTPS hostname has been chosen.
2. TLS is valid and automatically renewed.
3. A standards-compliant OAuth 2.1 MCP resource-server/authorization flow is in front of `/mcp` (DCR/CIMD-compatible where required by the client).
4. The MCP Python service remains bound to `127.0.0.1:8790` only.
5. Rate limiting and request-size limits are enabled at the public edge.
6. Access logs redact bearer tokens and never log Strategy Factory secrets.
7. The public MCP app exposes only `send_to_gpt`.
8. A live test proves `DELIVERED`, `ACKNOWLEDGED`, `REPLIED` and a matching durable Strategy Factory message ID.
9. No learnerbot restart or trading configuration change is coupled to MCP deployment.

## Gemini setup once eligible

In Gemini web:

1. Open **Settings & help → Connected Apps**.
2. Under **Custom apps for Spark**, add the public HTTPS MCP server URL.
3. Complete the OAuth connection flow.
4. In a Spark task, select the custom app with `@` and ask, for example:

`Use Strategy Factory GPT Bridge to send GPT: What is the current time in London? Return GPT's reply.`

Expected tool result fields include:

```json
{
  "message_id": "gemini-to-gpt-...",
  "from": "gemini",
  "to": "gpt",
  "delivered": true,
  "acknowledged": true,
  "status": "REPLIED",
  "gpt_reply": "...",
  "error": ""
}
```
