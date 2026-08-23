from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from learnerbot import telegram_master_change_patch as telegram_patch
from scripts import ai_agent_ws_bus as base_bus
from scripts import ai_agent_ws_bus_grok as broker_bridge
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
    assert '_sf.exchange("master", agent, body, subject=subject' in telegram
    assert "websockets.asyncio" not in cli
    assert 'if cmd == "/aichat"' in telegram


def test_telegram_subject_syntax_is_optional_and_bounded() -> None:
    assert telegram_patch._parse_chat_body("plain question") == ("", "plain question")
    assert telegram_patch._parse_chat_body("[HOOD fraud] review the latest finding") == (
        "HOOD fraud",
        "review the latest finding",
    )
    assert telegram_patch._parse_chat_body("[Server latency] compare p95") == (
        "Server latency",
        "compare p95",
    )


def test_docs_make_browser_sessions_explicitly_external() -> None:
    text = _text("AI_AGENT_MESSAGING.md")
    assert "Canonical user-to-agent chat identity" in text
    assert "external/unlinked session" in text
    assert "/aichat gemini" in text
    assert "not a seventh AI worker" in text
