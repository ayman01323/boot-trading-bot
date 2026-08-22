from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import ai_mailbox_telegram_notify as notify


@pytest.mark.parametrize("agent", ["claude", "gemini", "deepseek", "copilot"])
def test_build_message_supports_all_agents(agent: str) -> None:
    text = notify.build_message(agent, "reply", "msg-1", "COMPLETED")
    assert agent.upper() in text
    assert "Message ID: msg-1" in text
    assert "Status: COMPLETED" in text
    assert "Tell GPT: check" in text


def test_initiation_wording_is_metadata_only() -> None:
    text = notify.build_message("gemini", "initiation", "gemini-2", "REQUEST")
    assert "GEMINI → GPT MESSAGE" in text
    assert "New agent message received for GPT." in text
    assert "message body" not in text.lower()


def test_status_can_be_read_from_generated_reply(tmp_path: Path) -> None:
    path = tmp_path / "reply.md"
    path.write_text(
        "DEEPSEEK_TO_GPT\nin_reply_to: x1\nstatus: BLOCKED\nprovider_return_code: 1\n\nbody that must not be sent\n",
        encoding="utf-8",
    )
    assert notify.status_from_file(str(path), "UNKNOWN") == "BLOCKED"
    text = notify.build_message("deepseek", "reply", "x1", notify.status_from_file(str(path), "UNKNOWN"))
    assert "body that must not be sent" not in text


def test_invalid_agent_and_message_id_are_rejected() -> None:
    with pytest.raises(ValueError):
        notify.build_message("other", "reply", "x1", "COMPLETED")
    with pytest.raises(ValueError):
        notify.build_message("claude", "reply", "bad id", "COMPLETED")


def test_skip_if_unconfigured_does_not_attempt_network(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_MASTER_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai_mailbox_telegram_notify.py",
            "--agent",
            "claude",
            "--kind",
            "initiation",
            "--message-id",
            "x1",
            "--status",
            "REQUEST",
            "--skip-if-unconfigured",
        ],
    )
    assert notify.main() == 0
    assert "telegram_configured=false" in capsys.readouterr().out


def test_source_never_reads_runtime_env_file() -> None:
    text = Path("scripts/ai_mailbox_telegram_notify.py").read_text(encoding="utf-8")
    assert "load_dotenv" not in text
    assert "/root/" not in text
    assert "sudo" not in text
    assert "OPENAI_API_KEY" not in text
    assert "GEMINI_API_KEY" not in text
    assert "DEEPSEEK_API_KEY" not in text
