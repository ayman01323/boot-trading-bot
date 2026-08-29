from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scripts import strategy_factory_mcp_core as core

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_external_message_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_MCP_MAX_MESSAGE_CHARS", raising=False)
    assert core.normalise_external_message("  hello  ") == "hello"
    with pytest.raises(ValueError, match="cannot be empty"):
        core.normalise_external_message("  ")
    with pytest.raises(ValueError, match="exceeds"):
        core.normalise_external_message("x" * (core.DEFAULT_MAX_MESSAGE_CHARS + 1))


def test_guard_forces_communication_only_handling() -> None:
    guarded = core.build_guarded_message("What is the time in London?")
    assert "communication-only" in guarded
    assert "Do not edit files" in guarded
    assert "trade" in guarded
    assert "wallets/signing" in guarded
    assert "What is the time in London?" in guarded
    assert "<external_message>" in guarded


def test_send_to_gpt_uses_single_strategy_factory_transport(monkeypatch) -> None:
    seen = {}

    async def fake_exchange(sender, target, body, *, timeout):
        seen.update(sender=sender, target=target, body=body, timeout=timeout)
        return {
            "message_id": "gemini-to-gpt-test",
            "delivered": True,
            "acknowledged": True,
            "status": "REPLIED",
            "body": "3:45 PM BST",
            "error": "",
        }

    monkeypatch.setattr(core, "exchange", fake_exchange)
    monkeypatch.delenv("STRATEGY_MCP_TIMEOUT_SECONDS", raising=False)
    result = asyncio.run(core.send_to_gpt("What is the time in London?"))

    assert seen["sender"] == "gemini"
    assert seen["target"] == "gpt"
    assert seen["timeout"] == core.DEFAULT_TIMEOUT_SECONDS
    assert "communication-only" in seen["body"]
    assert result == {
        "message_id": "gemini-to-gpt-test",
        "from": "gemini",
        "to": "gpt",
        "delivered": True,
        "acknowledged": True,
        "status": "REPLIED",
        "gpt_reply": "3:45 PM BST",
        "error": "",
    }


def test_bridge_refuses_direct_public_bind_and_has_no_execution_primitives() -> None:
    text = _text("scripts/strategy_factory_mcp_bridge.py")
    low = text.lower()
    assert 'STRATEGY_MCP_HOST", "127.0.0.1"' in text
    assert "Refusing non-loopback bind" in text
    assert 'transport="streamable-http"' in text
    assert "stateless_http=True" in text
    assert "json_response=True" in text
    assert "@mcp.tool()" in text
    assert "async def send_to_gpt" in text
    assert "subprocess" not in low
    assert "os.system" not in low
    assert "run_shell_command" not in low


def test_mcp_dependencies_are_isolated_from_trading_runtime() -> None:
    bridge_requirements = _text("requirements-mcp-bridge.txt")
    trading_requirements = _text("requirements.txt")
    assert "mcp>=2.0,<3" in bridge_requirements
    assert "mcp" not in trading_requirements.lower()


def test_bridge_transport_files_are_governance_protected() -> None:
    policy_text = _text("scripts/master_change_policy.py")
    for path in (
        "scripts/strategy_factory_mcp_core.py",
        "scripts/strategy_factory_mcp_bridge.py",
        "tests/test_strategy_factory_mcp_bridge.py",
    ):
        assert f'"{path}"' in policy_text
