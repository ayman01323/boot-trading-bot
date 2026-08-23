from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from learnerbot import strategy_factory_council_transport_patch as council_transport
from scripts import ai_agent_ws_send as direct_sender
from scripts import master_change_policy as policy
from scripts import strategy_factory_transport as transport

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_strategy_factory_has_one_six_agent_transport() -> None:
    assert set(transport.AGENTS) == {"gpt", "claude", "gemini", "deepseek", "grok", "copilot"}
    assert direct_sender.AGENTS is transport.AGENTS
    assert "claude" not in transport.PUBLIC_TARGETS
    assert "claude-general" in transport.PUBLIC_TARGETS
    assert "claude-coding" in transport.PUBLIC_TARGETS
    assert transport.DEFAULT_URL == "ws://127.0.0.1:8765"


def test_claude_transport_requires_explicit_division() -> None:
    with pytest.raises(ValueError, match="Claude division required"):
        transport._route_target("claude", "review this")

    target, body, division = transport._route_target("claude-general", "review this")
    assert target == "claude"
    assert division == "GENERAL"
    assert "CLAUDE_DIVISION: GENERAL" in body
    assert "CLAUDE_IDENTITY: AUTOMATED_GENERAL" in body

    with pytest.raises(ValueError, match="Claude Coding is not"):
        transport._route_target("claude-coding", "fix this")


def test_direct_cli_delegates_transport_instead_of_opening_own_socket() -> None:
    text = _text("scripts/ai_agent_ws_send.py")
    assert "PUBLIC_TARGETS" in text
    assert "await exchange(" in text
    assert "websockets.asyncio.client" not in text
    assert "publish_coding_request" in text


def test_council_adapter_is_installed_on_same_transport() -> None:
    assert council_transport._base._ask_one is council_transport._ask_one
    text = _text("learnerbot/strategy_factory_council_transport_patch.py")
    assert "from scripts.strategy_factory_transport import exchange" in text
    assert '"routing_mode": "COUNCIL"' in text
    assert '"transport": "strategy-factory-websocket"' in text
    assert "AUTOMATED_GENERAL" in text


def test_council_adapter_correlates_ack_and_reply(monkeypatch) -> None:
    seen = {}

    async def fake_exchange(sender, target, body, *, message_id, timeout):
        seen.update(sender=sender, target=target, body=body, message_id=message_id, timeout=timeout)
        return {
            "message_id": message_id,
            "acknowledged": True,
            "status": "REPLIED",
            "body": "APPROVE: bounded change",
            "error": "",
        }

    monkeypatch.setattr(council_transport, "exchange", fake_exchange)
    row = asyncio.run(council_transport._ask_one("gemini", "mc-test", "review this", 2, timeout=9))
    assert seen["sender"] == "gpt"
    assert seen["target"] == "gemini"
    assert seen["body"] == "review this"
    assert seen["timeout"] == 9
    assert row["acknowledged"] is True
    assert row["provider_rc"] == 0
    assert row["reply"] == "APPROVE: bounded change"
    assert row["routing_mode"] == "COUNCIL"


def test_claude_council_request_is_general(monkeypatch) -> None:
    seen = {}

    async def fake_exchange(sender, target, body, *, message_id, timeout):
        seen.update(sender=sender, target=target, body=body)
        return {
            "message_id": message_id,
            "acknowledged": True,
            "status": "REPLIED",
            "body": "reviewed",
            "error": "",
        }

    monkeypatch.setattr(council_transport, "exchange", fake_exchange)
    row = asyncio.run(council_transport._ask_one("claude", "mc-test", "review architecture", 1))
    assert seen["target"] == "claude"
    assert "CLAUDE_DIVISION: GENERAL" in seen["body"]
    assert row["claude_division"] == "GENERAL"
    assert row["claude_identity"] == "AUTOMATED_GENERAL"


def test_unified_transport_is_governance_protected() -> None:
    for path in (
        "scripts/strategy_factory_transport.py",
        "scripts/ai_agent_ws_send.py",
        "scripts/claude_division.py",
        "learnerbot/strategy_factory_council_transport_patch.py",
        "learnerbot/telegram_master_change_patch.py",
        "AI_AGENT_MESSAGING.md",
        "CLAUDE.md",
        "tests/test_strategy_factory_transport.py",
        "tests/test_strategy_factory_master_chat.py",
    ):
        assert path in policy.GOVERNANCE_FILES


def test_documentation_defines_one_transport_two_modes_and_claude_divisions() -> None:
    text = _text("AI_AGENT_MESSAGING.md")
    assert "one primary messaging transport" in text
    assert "DIRECT mode" in text
    assert "COUNCIL mode" in text
    assert "not a second normal messaging system" in text
    assert "CLAUDE GENERAL" in text
    assert "CLAUDE CODING" in text
    assert "Grok" in text
