from __future__ import annotations

import os

import pytest

from scripts import ai_mailbox_provider_relay as relay


def _message(provider: str, message_id: str = "test-1") -> str:
    return (
        f"GPT_TO_{provider.upper()}\n"
        f"message_id: {message_id}\n"
        "source_sha: 0123456789abcdef0123456789abcdef01234567\n"
        "status: REQUEST\n\n"
        "Please report mailbox health only.\n"
    )


def test_relay_formats_completed_reply_and_redacts_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "call_provider", lambda provider, prompt: (0, "ok sk-abcdefghijklmnop", ""))
    out = relay.relay("deepseek", "test-1", "0123", _message("deepseek"))
    assert out.startswith("DEEPSEEK_TO_GPT\n")
    assert "in_reply_to: test-1" in out
    assert "status: COMPLETED" in out
    assert "provider_return_code: 0" in out
    assert "sk-abcdefghijklmnop" not in out
    assert "[REDACTED]" in out


def test_kimi_is_supported_by_bounded_provider_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "call_provider", lambda provider, prompt: (0, "kimi ok", ""))
    out = relay.relay("kimi", "test-1", "0123", _message("kimi"))
    assert out.startswith("KIMI_TO_GPT\n")
    assert "status: COMPLETED" in out


def test_relay_rejects_wrong_prefix_or_non_request() -> None:
    with pytest.raises(ValueError, match="prefix"):
        relay.relay("gemini", "test-1", "", _message("deepseek"))

    text = _message("gemini").replace("status: REQUEST", "status: READY")
    with pytest.raises(ValueError, match="REQUEST"):
        relay.relay("gemini", "test-1", "", text)


def test_relay_rejects_unsafe_message_id() -> None:
    with pytest.raises(ValueError, match="message id"):
        relay.relay("deepseek", "bad id with spaces", "", _message("deepseek", "bad id with spaces"))


def test_copilot_runs_provider_from_empty_temp_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.getcwd()
    seen: dict[str, str] = {}

    def fake_call(provider: str, prompt: str):
        seen["cwd"] = os.getcwd()
        seen["provider"] = provider
        return 0, "copilot reply", ""

    monkeypatch.setattr(relay, "call_provider", fake_call)
    out = relay.relay("copilot", "test-1", "", _message("copilot"))
    assert seen["provider"] == "copilot"
    assert seen["cwd"] != original
    assert os.getcwd() == original
    assert "status: COMPLETED" in out


def test_blocked_reply_sanitises_provider_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "secret-value-123456")
    out = relay._blocked_reply("kimi", "test-1", RuntimeError("failed with secret-value-123456"))
    assert out.startswith("KIMI_TO_GPT\n")
    assert "status: BLOCKED" in out
    assert "secret-value-123456" not in out
    assert "[REDACTED]" in out
