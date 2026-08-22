from __future__ import annotations

from pathlib import Path

import pytest

from scripts import ai_mailbox_telegram_notify as notify


@pytest.mark.parametrize("agent", ["gpt", "claude", "gemini", "deepseek", "copilot"])
def test_build_message_supports_all_agents(agent: str) -> None:
    text = notify.build_message(agent, "reply", "msg-1", "COMPLETED", "STATELESS_API_RESPONDER")
    assert agent.upper() in text
    assert "Message ID: msg-1" in text
    assert "Status: COMPLETED" in text
    assert "Strategy Room delivery notifications: ON" in text


def test_persistent_agent_and_api_responder_are_visibly_different() -> None:
    persistent = notify.build_message("claude", "initiation", "claude-2", "REQUEST", "PERSISTENT_AGENT")
    api = notify.build_message("claude", "api_reply", "claude-2", "RESPONSE", "STATELESS_API_RESPONDER")
    assert "CLAUDE AGENT → GPT MESSAGE" in persistent
    assert "Identity: PERSISTENT_AGENT" in persistent
    assert "CLAUDE API RESPONDER → GPT REPLY" in api
    assert "not the persistent/interactive agent" in api
    assert "Identity: STATELESS_API_RESPONDER" in api


def test_delivery_receipt_says_target_can_process_without_polling() -> None:
    text = notify.build_message("deepseek", "delivery", "d-1", "REQUEST", "STATELESS_API_TARGET")
    assert "GPT → 🔴 DEEPSEEK MESSAGE DELIVERED" in text
    assert "without polling" in text


def test_status_can_be_read_from_generated_reply(tmp_path: Path) -> None:
    path = tmp_path / "reply.md"
    path.write_text("DEEPSEEK_TO_GPT\nin_reply_to: x1\nstatus: BLOCKED\nprovider_return_code: 1\n\nbody that must not be sent\n", encoding="utf-8")
    assert notify.status_from_file(str(path), "UNKNOWN") == "BLOCKED"
    text = notify.build_message("deepseek", "reply", "x1", notify.status_from_file(str(path), "UNKNOWN"))
    assert "body that must not be sent" not in text


def test_invalid_agent_and_message_id_are_rejected() -> None:
    with pytest.raises(ValueError): notify.build_message("other", "reply", "x1", "COMPLETED")
    with pytest.raises(ValueError): notify.build_message("claude", "reply", "bad id", "COMPLETED")


def test_skip_if_unconfigured_does_not_attempt_network(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_MASTER_CHAT_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["ai_mailbox_telegram_notify.py", "--agent", "claude", "--kind", "initiation", "--message-id", "x1", "--status", "REQUEST", "--skip-if-unconfigured"])
    assert notify.main() == 0
    assert "telegram_configured=false" in capsys.readouterr().out


def test_source_never_reads_runtime_env_file() -> None:
    text = Path("scripts/ai_mailbox_telegram_notify.py").read_text(encoding="utf-8")
    assert "load_dotenv" not in text
    assert "/root/" not in text
    assert "sudo" not in text
    for secret in ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        assert secret not in text
