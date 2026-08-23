from __future__ import annotations

import asyncio

import pytest

from scripts import claude_division as claude
from scripts import strategy_factory_transport as transport


def test_bare_claude_is_rejected_for_public_chat() -> None:
    with pytest.raises(ValueError, match="division required"):
        claude.parse_chat_target("claude")
    assert claude.parse_chat_target("claude-general") == ("claude", "general")
    assert claude.parse_chat_target("claude-coding") == ("claude", "coding")


def test_transport_general_is_tagged_and_coding_has_no_websocket_fallback() -> None:
    target, body, division = transport._route_target("claude-general", "review this")
    assert target == "claude"
    assert division == "GENERAL"
    assert "CLAUDE_DIVISION: GENERAL" in body
    assert "AUTOMATED_GENERAL" in body
    with pytest.raises(ValueError, match="not a Strategy Factory WebSocket recipient"):
        transport._route_target("claude-coding", "fix this")
    with pytest.raises(ValueError, match="division required"):
        transport._route_target("claude", "ambiguous")


def test_subject_thread_survives_claude_general_routing() -> None:
    thread_id, subject = transport.resolve_thread(subject="Execution latency")
    assert subject == "Execution latency"
    assert thread_id.startswith("thr-execution-latency-")
    target, body, division = transport._route_target("claude-general", "challenge hypothesis")
    assert target == "claude"
    assert division == "GENERAL"
    assert "challenge hypothesis" in body


def test_coding_reply_must_match_division_identity_and_correlation() -> None:
    expected = "master-to-claude-coding-20260823T170000Z-abcd"
    valid = (
        "CLAUDE_TO_GPT\n"
        "message_id: response-1\n"
        "division: CODING\n"
        "identity: PERSISTENT_AGENT\n"
        "status: RESPONSE\n"
        f"in_reply_to: {expected}\n\n"
        "Reviewed.\n"
    )
    row = claude.validate_coding_reply(valid, expected_message_id=expected)
    assert row["division"] == "CODING"
    assert row["identity"] == "PERSISTENT_AGENT"
    assert row["verification"] == "HEADER_VERIFIED_NOT_CRYPTOGRAPHIC"

    no_identity = valid.replace("identity: PERSISTENT_AGENT\n", "")
    with pytest.raises(ValueError, match="UNVERIFIED_CLAUDE_CODING_REPLY"):
        claude.validate_coding_reply(no_identity, expected_message_id=expected)

    wrong_division = valid.replace("division: CODING", "division: GENERAL")
    with pytest.raises(ValueError, match="UNVERIFIED_CLAUDE_CODING_REPLY"):
        claude.validate_coding_reply(wrong_division, expected_message_id=expected)

    with pytest.raises(ValueError, match="correlation mismatch"):
        claude.validate_coding_reply(valid, expected_message_id="master-to-claude-coding-20260823T170001Z-eeee")


def test_non_claude_public_targets_are_preserved() -> None:
    for name in ("gpt", "gemini", "deepseek", "grok", "copilot"):
        assert name in transport.PUBLIC_TARGETS
        target, body, division = transport._route_target(name, "hello")
        assert target == name
        assert body == "hello"
        assert division == ""
    assert "claude" not in transport.PUBLIC_TARGETS
