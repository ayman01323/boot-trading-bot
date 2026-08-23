from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts import ai_agent_ws_bus as base_bus
from scripts import ai_agent_ws_bus_grok as broker_bridge
from scripts import claude_division
from scripts import strategy_factory_transport as transport

ROOT = Path(__file__).resolve().parents[1]


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_master_is_sender_only_and_not_seventh_agent() -> None:
    expected = {"gpt", "claude", "gemini", "deepseek", "grok", "copilot"}
    assert set(transport.AGENTS) == expected
    assert set(broker_bridge.AGENTS) == expected
    assert "master" in transport.SENDERS
    assert "master" in broker_bridge.CLIENT_IDENTITIES
    assert "master" not in transport.AGENTS
    assert "master" not in broker_bridge.AGENTS


def test_broker_accepts_master_registration_but_not_master_as_target(tmp_path) -> None:
    broker = base_bus.Broker(base_bus.Store(str(tmp_path / "bus.sqlite3")))
    ws = _FakeWS()
    asyncio.run(broker._register(ws, {"agent": "master", "token": ""}))
    assert broker.reverse[ws] == "master"
    assert ws.sent == [{"type": "registered", "agent": "master"}]
    with pytest.raises(base_bus.BusError):
        base_bus._normalise_agent("master")


def test_master_chat_frontends_delegate_to_shared_transport() -> None:
    cli = _text("scripts/strategy_factory_chat.py")
    telegram = _text("learnerbot/telegram_master_change_patch.py")
    assert 'exchange(\n        "master",' in cli
    assert '_sf.exchange("master", agent, body' in telegram
    assert "websockets.asyncio" not in cli
    assert 'if cmd == "/aichat"' in telegram


def test_claude_operator_chat_requires_explicit_division() -> None:
    with pytest.raises(ValueError, match="division required"):
        claude_division.parse_chat_target("claude")
    assert claude_division.parse_chat_target("claude-general") == ("claude", "general")
    assert claude_division.parse_chat_target("claude-coding") == ("claude", "coding")
    assert claude_division.parse_chat_target("gemini") == ("gemini", "")


def test_claude_general_messages_are_identity_tagged() -> None:
    text = claude_division.general_message("Review the governance design")
    assert text.startswith("CLAUDE_DIVISION: GENERAL\nCLAUDE_IDENTITY: AUTOMATED_GENERAL")
    assert "Review the governance design" in text


def test_claude_coding_request_requires_persistent_identity() -> None:
    message_id, text = claude_division.build_coding_request(
        "Inspect this repository defect",
        message_id="test-claude-coding-001",
        requested_by="MASTER",
        source_sha="a" * 40,
    )
    assert message_id == "test-claude-coding-001"
    assert text.startswith("GPT_TO_CLAUDE\n")
    assert "division: CODING\n" in text
    assert "identity_required: PERSISTENT_AGENT\n" in text
    assert "requested_by: MASTER\n" in text
    assert "Inspect this repository defect" in text


def test_frontends_show_two_claude_divisions_and_no_bare_choice() -> None:
    cli = _text("scripts/strategy_factory_chat.py")
    telegram = _text("learnerbot/telegram_master_change_patch.py")
    assert '"claude-general", "claude-coding"' in cli
    assert "claude-general" in telegram
    assert "claude-coding" in telegram
    assert "bare <code>claude</code> is intentionally rejected" in telegram


def test_council_claude_is_explicitly_general() -> None:
    text = _text("learnerbot/strategy_factory_council_transport_patch.py")
    assert 'division = "GENERAL"' in text
    assert "AUTOMATED_GENERAL" in text
    assert "general_message(body)" in text


def test_docs_make_browser_sessions_explicitly_external() -> None:
    text = _text("AI_AGENT_MESSAGING.md")
    assert "Canonical user-to-agent chat identity" in text
    assert "external/unlinked session" in text
    assert "/aichat gemini" in text
    assert "not a seventh AI worker" in text
    assert "CLAUDE GENERAL" in text
    assert "CLAUDE CODING" in text
