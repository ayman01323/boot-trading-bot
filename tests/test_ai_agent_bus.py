from __future__ import annotations

import os

import pytest

from scripts import ai_agent_bus as bus


def _message(*, sender: str = "GPT", target: str = "CLAUDE", mode: str = "DIRECT", max_hops: int = 1) -> str:
    return (
        "AI_BUS\n"
        "message_id: test-2026-08-21\n"
        f"from: {sender}\n"
        f"to: {target}\n"
        f"mode: {mode}\n"
        f"max_hops: {max_hops}\n\n"
        "Please answer this bounded communication test.\n"
    )


def test_parse_direct_message_caps_hops() -> None:
    env = bus.parse_envelope(_message(mode="DIRECT", max_hops=3))
    assert env.sender == "gpt"
    assert env.target == "claude"
    assert env.mode == "direct"
    assert env.max_hops == 1
    assert not bus.needs_copilot(env)


def test_collaboration_requires_copilot_setup_for_possible_route() -> None:
    env = bus.parse_envelope(_message(mode="COLLABORATE", max_hops=2))
    assert env.max_hops == 2
    assert bus.needs_copilot(env)


def test_rejects_invalid_or_oversized_envelope() -> None:
    with pytest.raises(ValueError, match="message_id"):
        bus.parse_envelope(_message().replace("test-2026-08-21", "bad id"))
    with pytest.raises(ValueError, match="between 1 and 3"):
        bus.parse_envelope(_message(mode="COLLABORATE", max_hops=4))
    with pytest.raises(ValueError, match="to must be"):
        bus.parse_envelope(_message(target="UNKNOWN"))


def test_direct_message_calls_only_addressed_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_call(provider: str, prompt: str):
        calls.append(provider)
        return 0, "Claude received it.\nROUTE_TO: GEMINI\nROUTE_QUESTION: unnecessary", ""

    monkeypatch.setattr(bus, "call_provider", fake_call)
    reply = bus.run_bus(bus.parse_envelope(_message()))
    assert calls == ["claude"]
    assert "provider_calls: 1" in reply
    assert "### CLAUDE" in reply
    assert "ROUTE_TO" not in reply


def test_collaboration_routes_with_hard_hop_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_call(provider: str, prompt: str):
        calls.append(provider)
        if provider == "claude":
            return 0, "Ask Gemini.\nROUTE_TO: GEMINI\nROUTE_QUESTION: Check this conclusion.", ""
        return 0, "Gemini confirms.\nROUTE_TO: CLAUDE\nROUTE_QUESTION: Continue looping.", ""

    monkeypatch.setattr(bus, "call_provider", fake_call)
    env = bus.parse_envelope(_message(mode="COLLABORATE", max_hops=2))
    reply = bus.run_bus(env)
    assert calls == ["claude", "gemini"]
    assert "provider_calls: 2" in reply
    assert "### CLAUDE · hop 1" in reply
    assert "### GEMINI · hop 2" in reply


def test_explicit_all_broadcast_does_not_recursively_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_call(provider: str, prompt: str):
        calls.append(provider)
        return 0, f"reply from {provider}\nROUTE_TO: COPILOT\nROUTE_QUESTION: should not route", ""

    monkeypatch.setattr(bus, "call_provider", fake_call)
    env = bus.parse_envelope(_message(sender="USER", target="ALL", mode="COLLABORATE", max_hops=3))
    assert env.max_hops == 1
    reply = bus.run_bus(env)
    assert calls == list(bus.AGENTS)
    assert "provider_calls: 5" in reply


def test_exact_environment_secret_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-this-is-a-private-test-key-123456789")
    monkeypatch.setattr(
        bus,
        "call_provider",
        lambda provider, prompt: (0, "echo AIza-this-is-a-private-test-key-123456789", ""),
    )
    reply = bus.run_bus(bus.parse_envelope(_message(target="GEMINI")))
    assert "AIza-this-is-a-private-test-key-123456789" not in reply
    assert "[REDACTED]" in reply


def test_copilot_runs_outside_repo_and_without_other_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.getcwd()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-test")
    monkeypatch.setenv("COPILOT_ASSIGN_TOKEN", "copilot-token-test")
    seen: dict[str, object] = {}

    def fake_call(provider: str, prompt: str):
        seen["provider"] = provider
        seen["cwd"] = os.getcwd()
        seen["openai"] = os.environ.get("OPENAI_API_KEY")
        seen["anthropic"] = os.environ.get("ANTHROPIC_API_KEY")
        seen["gemini"] = os.environ.get("GEMINI_API_KEY")
        seen["deepseek"] = os.environ.get("DEEPSEEK_API_KEY")
        seen["copilot"] = os.environ.get("COPILOT_ASSIGN_TOKEN")
        return 0, "copilot reply", ""

    monkeypatch.setattr(bus, "call_provider", fake_call)
    reply = bus.run_bus(bus.parse_envelope(_message(target="COPILOT")))
    assert seen["provider"] == "copilot"
    assert seen["cwd"] != original
    assert seen["openai"] is None
    assert seen["anthropic"] is None
    assert seen["gemini"] is None
    assert seen["deepseek"] is None
    assert seen["copilot"] == "copilot-token-test"
    assert os.environ.get("OPENAI_API_KEY") == "openai-secret-test"
    assert os.getcwd() == original
    assert "status: COMPLETED" in reply
